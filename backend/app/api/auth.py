"""用户认证 REST 接口。

- POST /api/auth/register: 注册（昵称 + 可选 UID），返回 JWT
- POST /api/auth/login: 用现有 UID 登录，返回 JWT
- POST /api/auth/refresh: 用 refresh token 换取新的 access token
- GET /api/auth/profile: 查询当前用户档案（需 JWT）
- GET /api/auth/exists?uid=xxx: 检查 UID 是否存在
- POST /api/auth/generate-uid: 仅生成一个 UID（不落库）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.auth import (
    generate_uid,
    get_user,
    issue_access_token,
    issue_refresh_token,
    register_user,
    user_exists,
    verify_refresh_token,
)
from app.models.schemas import (
    ApiResponse,
    ExistsResponse,
    LoginRequest,
    LoginResponse,
    ProfileResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, db: DbSession) -> ApiResponse:
    """注册新用户或获取已有用户。

    典型流程：
    1. 客户端首次打开 → POST /api/auth/register {nickname} → 拿到 uid + JWT
    2. 客户端本地存储 uid 和 refresh_token（localStorage）
    3. 后续每次访问 REST → 携带 access_token（Authorization: Bearer xxx）
    4. access_token 过期 → POST /api/auth/refresh 换取新 access_token
    5. refresh_token 过期 → 重新走第 1 步
    """
    try:
        uid, is_new = await register_user(db, req.nickname, preferred_uid=req.uid)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    user_info = await get_user(db, uid)
    nickname = user_info["nickname"] if user_info else req.nickname

    return ApiResponse(data=RegisterResponse(
        uid=uid,
        nickname=nickname,
        is_new=is_new,
        access_token=issue_access_token(uid, nickname),
        refresh_token=issue_refresh_token(uid),
    ).model_dump())


@router.post("/login", response_model=ApiResponse)
async def login(req: LoginRequest, db: DbSession) -> ApiResponse:
    """用现有 UID 登录（换取新的 JWT 对）。"""
    user_info = await get_user(db, req.uid)
    if user_info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return ApiResponse(data=LoginResponse(
        uid=user_info["uid"],
        nickname=user_info["nickname"],
        access_token=issue_access_token(user_info["uid"], user_info["nickname"]),
        refresh_token=issue_refresh_token(user_info["uid"]),
    ).model_dump())


@router.post("/refresh", response_model=ApiResponse)
async def refresh(req: RefreshRequest, db: DbSession) -> ApiResponse:
    """用 refresh token 换取新的 access token。"""
    payload = verify_refresh_token(req.refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token 无效或已过期",
        )
    uid = payload["sub"]
    if not await user_exists(db, uid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    return ApiResponse(data=RefreshResponse(
        access_token=issue_access_token(uid, payload.get("nickname", "")),
    ).model_dump())


@router.get("/profile", response_model=ApiResponse)
async def profile(user_uid: CurrentUser, db: DbSession) -> ApiResponse:
    """查询当前用户档案（需 JWT）。"""
    user_info = await get_user(db, user_uid)
    if user_info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return ApiResponse(data=ProfileResponse(**user_info).model_dump())


@router.get("/exists", response_model=ApiResponse)
async def exists(uid: str = Query(..., min_length=4, max_length=16)) -> ApiResponse:
    """检查 UID 是否存在（无需认证，便于客户端注册前预检）。"""
    from app.models.db import get_session
    async with get_session() as session:
        ok = await user_exists(session, uid)
    return ApiResponse(data=ExistsResponse(uid=uid, exists=ok).model_dump())


@router.post("/generate-uid", response_model=ApiResponse)
async def generate_uid_endpoint() -> ApiResponse:
    """仅生成一个 UID（不落库）。

    客户端在用户输入昵称前可预先生成 UID，避免重复注册。
    """
    return ApiResponse(data={"uid": generate_uid()})
