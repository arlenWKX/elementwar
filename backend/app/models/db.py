"""SQLAlchemy 2.0 异步 ORM 模型与会话工厂。

表结构：
- user_profile: 用户档案（uid 主键，无密码，JWT subject）
- reaction_cache: 反应结果缓存（按 reactants+conditions 指纹）
- session_token: 断线重连 short-lived 令牌（与 JWT access token 区分）

API 设计：
- get_session(): async context manager，业务层统一通过它获取会话
- get_session_factory(): FastAPI Depends 用，返回 async_sessionmaker
- get_db(): FastAPI Depends 依赖，注入 AsyncSession

不再暴露 _build_engine() 等内部 API。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import DATA_DIR, settings


# ============================================================
# 基类
# ============================================================
class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _now() -> datetime:
    """返回带时区的 UTC 当前时间。"""
    return datetime.now(timezone.utc)


# ============================================================
# 用户档案
# ============================================================
class UserProfile(Base):
    """用户档案。

    简化认证模型：UID 作为主键，无密码。
    JWT 的 subject 字段 = uid。
    """

    __tablename__ = "user_profile"

    uid: Mapped[str] = mapped_column(String(16), primary_key=True)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    games_played: Mapped[int] = mapped_column(default=0)

    __table_args__ = (
        Index("ix_user_profile_nickname", "nickname"),
    )


# ============================================================
# 反应结果缓存
# ============================================================
class ReactionCache(Base):
    """反应结果缓存（持久层备份，主要走内存 LRU）。

    将 (sorted_reactants, conditions) 哈希为唯一键，避免重复计算。
    """

    __tablename__ = "reaction_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reactants_json: Mapped[str] = mapped_column(Text, nullable=False)
    conditions_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_hit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=0)


# ============================================================
# 断线重连 short-lived 令牌
# ============================================================
class SessionToken(Base):
    """断线重连令牌（与 JWT access token 区分）。

    场景：玩家游戏中网络抖动断线，前端持有 JWT 但 Socket.IO 连接断开。
    可选择用 reconnect_token 快速恢复房间绑定关系，无需重新走 HTTP 创建/加入房间流程。
    TTL 由 game_config.reconnect_token_ttl_sec 控制（默认 10 分钟），一次性使用。
    """

    __tablename__ = "session_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    room_id: Mapped[str] = mapped_column(String(32), index=True)
    player_id: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_session_token_room_player", "room_id", "player_id"),
    )


# ============================================================
# 引擎与会话工厂（私有，外部通过 get_session / get_db 访问）
# ============================================================
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """获取异步引擎单例。"""
    global _engine
    if _engine is not None:
        return _engine

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    engine_kwargs: dict = {"future": True}
    if "sqlite" in settings.database_url:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in settings.database_url:
            from sqlalchemy.pool import StaticPool
            engine_kwargs["poolclass"] = StaticPool

    _engine = create_async_engine(settings.database_url, **engine_kwargs)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取 session 工厂单例。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """异步上下文管理器：获取数据库会话。

    用法：
        async with get_session() as session:
            ...

    异常时回滚，正常退出时由调用方决定是否 commit。
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends 依赖：注入 AsyncSession。

    用法：
        @router.get("/profile")
        async def profile(db: AsyncSession = Depends(get_db)):
            ...
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ============================================================
# 启动/关闭钩子
# ============================================================
async def init_db() -> None:
    """启动时创建所有表（开发环境用，生产建议用 alembic 迁移）。"""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    """关闭时释放引擎资源。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


# ============================================================
# 工具函数
# ============================================================
def compute_fingerprint(reactants: list[tuple[str, float]], conditions: dict) -> str:
    """计算反应输入的指纹（SHA256）。

    Args:
        reactants: [(name, mol), ...]，自动按 name 排序
        conditions: 条件字典，排序后序列化

    Returns:
        64 字符的 hex 摘要
    """
    sorted_reactants = sorted(reactants, key=lambda x: x[0])
    canonical = json.dumps(
        {"reactants": sorted_reactants, "conditions": conditions},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_room_id() -> str:
    """生成房间 ID（8 位 hex 大写）。"""
    return uuid.uuid4().hex[:8].upper()


def generate_token() -> str:
    """生成一个安全的随机 token（URL safe, ~43 字符）。"""
    return secrets.token_urlsafe(32)


__all__ = [
    "Base",
    "UserProfile",
    "ReactionCache",
    "SessionToken",
    "get_session",
    "get_db",
    "init_db",
    "dispose_db",
    "compute_fingerprint",
    "new_room_id",
    "generate_token",
]
