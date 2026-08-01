"""FastAPI 依赖注入。

提供：
- get_db: 注入 AsyncSession
- get_current_user: 从 JWT 解析当前用户 uid
- require_user_exists: 校验用户存在并返回 uid
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import user_exists
from app.models.db import get_db


# 数据库会话依赖
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _extract_bearer_token(authorization: str | None) -> str | None:
    """从 Authorization 头提取 Bearer token。"""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


async def get_current_user(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> str:
    """从 Authorization 头解析 JWT，返回当前用户 uid。

    Raises:
        HTTPException 401: 未提供 token / token 无效 / 用户不存在
    """
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证 token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from app.auth import verify_access_token
    payload = verify_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    uid = payload.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 缺少 subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not await user_exists(db, uid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return uid


# 当前用户 uid 依赖
CurrentUser = Annotated[str, Depends(get_current_user)]


__all__ = ["DbSession", "CurrentUser", "get_current_user", "get_db"]
