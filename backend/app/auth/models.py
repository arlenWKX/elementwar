"""用户数据模型与认证服务。

设计：
- UserProfile: 用户档案（uid 主键，无密码）
- JWT 用于 REST API 鉴权（access token 1h + refresh token 30d）
- 断线重连 token 用于 Socket.IO 快速恢复房间绑定（short-lived 10min）
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, Index, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_game_config, settings
from app.models.db import Base
from app.services.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# ORM 模型（重导出，便于外部 import）
# ============================================================
from app.models.db import UserProfile  # noqa: E402,F401


# ============================================================
# JWT 工具
# ============================================================
def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 编码（无 padding）。"""
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """URL-safe base64 解码（自动补 padding）。"""
    import base64
    pad = 4 - (len(s) % 4)
    if pad != 4:
        s = s + ("=" * pad)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _jwt_encode(payload: dict[str, Any]) -> str:
    """HS256 签名生成 JWT。

    手写实现，避免引入 PyJWT 依赖（轻量优先）。
    """
    import hmac
    import hashlib
    import json

    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _jwt_verify(token: str) -> dict[str, Any] | None:
    """校验 JWT 签名 + 过期时间。返回 payload 或 None。"""
    import hmac
    import hashlib
    import json

    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None
    # 过期校验
    exp = payload.get("exp")
    if exp is not None and datetime.now(timezone.utc).timestamp() > exp:
        return None
    return payload


def issue_access_token(uid: str, nickname: str) -> str:
    """签发 access token（短期，默认 1 小时）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": uid,
        "nickname": nickname,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_min)).timestamp()),
    }
    return _jwt_encode(payload)


def issue_refresh_token(uid: str) -> str:
    """签发 refresh token（长期，默认 30 天）。

    refresh token 仅含 uid，用于换发新的 access token。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": uid,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_refresh_ttl_day)).timestamp()),
    }
    return _jwt_encode(payload)


def verify_access_token(token: str) -> dict[str, Any] | None:
    """校验 access token。"""
    payload = _jwt_verify(token)
    if payload is None:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def verify_refresh_token(token: str) -> dict[str, Any] | None:
    """校验 refresh token。"""
    payload = _jwt_verify(token)
    if payload is None:
        return None
    if payload.get("type") != "refresh":
        return None
    return payload


# ============================================================
# UID 生成
# ============================================================
def generate_uid(length: int | None = None) -> str:
    """生成一个 6 位 UID（字符表来自 game_config.auth.uid_alphabet）。"""
    cfg = get_game_config()
    alphabet = cfg.uid_alphabet
    n = length or cfg.uid_length
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ============================================================
# 用户业务操作
# ============================================================
async def register_user(
    session: AsyncSession,
    nickname: str,
    *,
    preferred_uid: str | None = None,
) -> tuple[str, bool]:
    """注册新用户。

    Args:
        session: 数据库会话
        nickname: 昵称（1-32 字符）
        preferred_uid: 客户端可指定 UID，冲突时返回新 UID

    Returns:
        (uid, is_new) — is_new=False 表示已存在该用户
    """
    nickname = (nickname or "").strip()[:32]
    if not nickname:
        raise ValueError("昵称不能为空")

    if preferred_uid:
        existing = await session.get(UserProfile, preferred_uid)
        if existing is not None:
            if existing.nickname == nickname:
                await session.execute(
                    update(UserProfile)
                    .where(UserProfile.uid == preferred_uid)
                    .values(last_seen_at=datetime.now(timezone.utc))
                )
                await session.commit()
                return preferred_uid, False
        else:
            session.add(UserProfile(uid=preferred_uid, nickname=nickname))
            await session.commit()
            logger.info("user.registered", extra={"uid": preferred_uid, "nickname": nickname})
            return preferred_uid, True

    for _ in range(10):
        uid = generate_uid()
        existing = await session.get(UserProfile, uid)
        if existing is None:
            session.add(UserProfile(uid=uid, nickname=nickname))
            await session.commit()
            logger.info("user.registered", extra={"uid": uid, "nickname": nickname})
            return uid, True

    raise RuntimeError("UID 生成碰撞超过 10 次，请重试")


async def get_user(session: AsyncSession, uid: str) -> dict[str, Any] | None:
    """查询用户档案（同时更新 last_seen_at）。"""
    user = await session.get(UserProfile, uid)
    if user is None:
        return None
    await session.execute(
        update(UserProfile)
        .where(UserProfile.uid == uid)
        .values(last_seen_at=datetime.now(timezone.utc))
    )
    await session.commit()
    return {
        "uid": user.uid,
        "nickname": user.nickname,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "games_played": user.games_played,
    }


async def user_exists(session: AsyncSession, uid: str) -> bool:
    """快速检查 UID 是否存在。"""
    user = await session.get(UserProfile, uid)
    return user is not None


async def get_user_nickname(session: AsyncSession, uid: str) -> str | None:
    """仅取昵称（不更新 last_seen）。"""
    user = await session.get(UserProfile, uid)
    return user.nickname if user else None


__all__ = [
    "UserProfile",
    "issue_access_token",
    "issue_refresh_token",
    "verify_access_token",
    "verify_refresh_token",
    "generate_uid",
    "register_user",
    "get_user",
    "user_exists",
    "get_user_nickname",
]
