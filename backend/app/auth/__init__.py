"""认证模块。

提供：
- JWT 签发与校验（access + refresh token）
- 用户注册、查询、UID 生成
- 断线重连 token 管理
"""

from __future__ import annotations

from app.auth.models import (
    UserProfile,
    generate_uid,
    get_user,
    get_user_nickname,
    issue_access_token,
    issue_refresh_token,
    register_user,
    user_exists,
    verify_access_token,
    verify_refresh_token,
)

__all__ = [
    "UserProfile",
    "issue_access_token",
    "issue_refresh_token",
    "verify_access_token",
    "verify_refresh_token",
    "generate_uid",
    "register_user",
    "get_user",
    "get_user_nickname",
    "user_exists",
]
