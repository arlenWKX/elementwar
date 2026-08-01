"""断线重连 token 管理。

设计：
- 玩家连接后下发 token
- 断线后用 token 重连，恢复到原房间原玩家
- token 有 TTL（game_config.reconnect_token_ttl_sec，默认 10 分钟）
- token 一次性使用（防重放）

与 JWT 的区别：
- JWT: 长期凭证，用于 REST API 鉴权
- reconnect_token: 一次性短期凭证，用于 Socket.IO 断线后快速恢复房间绑定
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_game_config
from app.models.db import SessionToken, generate_token


async def issue_token(
    session: AsyncSession,
    room_id: str,
    player_id: str,
) -> str:
    """下发重连 token。

    Args:
        session: 数据库会话
        room_id: 房间 ID
        player_id: 玩家 ID

    Returns:
        ~43 字符的 token 字符串
    """
    cfg = get_game_config()
    token = generate_token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=cfg.reconnect_token_ttl_sec)
    session.add(
        SessionToken(
            token=token,
            room_id=room_id,
            player_id=player_id,
            issued_at=now,
            expires_at=expires,
            used=False,
        )
    )
    await session.commit()
    return token


async def validate_and_consume_token(
    session: AsyncSession,
    token: str,
) -> tuple[str, str] | None:
    """校验并消费 token（一次性）。

    Returns:
        (room_id, player_id) 或 None（token 无效/已用/过期）
    """
    stmt = select(SessionToken).where(SessionToken.token == token)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    if row.used:
        return None
    # SQLite 默认不保留时区信息，从数据库读出的 datetime 可能是 naive
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return None
    await session.execute(
        update(SessionToken).where(SessionToken.id == row.id).values(used=True)
    )
    await session.commit()
    return row.room_id, row.player_id


__all__ = ["issue_token", "validate_and_consume_token"]
