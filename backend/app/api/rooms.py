"""房间管理 REST 接口。

- POST /api/rooms: 创建房间（需 JWT）
- POST /api/rooms/join: 通过匹配码加入（需 JWT）
- GET /api/rooms/{room_id}: 查询房间信息（需 JWT）
- DELETE /api/rooms/{room_id}: 销毁房间（需 JWT，仅限房内玩家）
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.auth import get_user_nickname
from app.connection.room_registry import get_registry
from app.models.schemas import (
    ApiResponse,
    CreateRoomRequest,
    CreateRoomResponse,
    JoinRoomRequest,
    JoinRoomResponse,
    RoomInfoResponse,
)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.post("", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_room(req: CreateRoomRequest, uid: CurrentUser, db: DbSession) -> ApiResponse:
    """创建房间。

    流程：
    1. JWT 解析当前 uid
    2. 从 DB 拉取昵称
    3. 创建房间并加入玩家
    4. 返回 room_id、code、reconnect_token

    注：AI 玩家不再由服务端自动添加。
    若需 vs AI 对战，client 端启动 AI Bot 程序，通过 /api/auth/register + /api/rooms/join
    以普通玩家身份加入房间。AI 与人类走完全相同的接口。
    """
    nickname = await get_user_nickname(db, uid) or "玩家"

    registry = get_registry()
    player_id = uid  # 直接用 uid 作为 player_id，简化映射
    room, token = await registry.create_room(
        player_id, nickname, vs_ai=False, max_players=req.total_players,
    )

    return ApiResponse(data=CreateRoomResponse(
        room_id=room.room_id,
        code=room.code,
        player_id=player_id,
        reconnect_token=token,
    ).model_dump())


@router.post("/join", response_model=ApiResponse)
async def join_room(req: JoinRoomRequest, uid: CurrentUser, db: DbSession) -> ApiResponse:
    """通过匹配码加入房间。"""
    nickname = await get_user_nickname(db, uid) or "玩家"

    registry = get_registry()
    player_id = uid
    result = await registry.join_room(req.code, player_id, nickname)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="房间不存在或已满",
        )
    room, token = result
    opponent = next((p for p in room.players if p.player_id != player_id), None)
    return ApiResponse(data=JoinRoomResponse(
        room_id=room.room_id,
        player_id=player_id,
        reconnect_token=token,
        opponent_name=opponent.name if opponent else None,
    ).model_dump())


@router.get("/my/active", response_model=ApiResponse)
async def list_my_active_rooms(uid: CurrentUser) -> ApiResponse:
    """列出当前玩家创建或加入的活跃房间（waiting/playing）。"""
    registry = get_registry()
    rooms = []
    for room in registry.iter_rooms():
        player = room.get_player(uid)
        if player is not None and room.phase.value in ("waiting", "playing"):
            rooms.append({
                "room_id": room.room_id,
                "code": room.code,
                "phase": room.phase.value,
                "players": [p.public_to_dict() for p in room.players],
                "current_player_id": room.current_player.player_id if room.current_player else None,
                "turn_no": room.turn_no,
            })
    return ApiResponse(data={"rooms": rooms})


@router.get("/{room_id}", response_model=ApiResponse)
async def get_room(room_id: str, uid: CurrentUser) -> ApiResponse:
    """查询房间信息。

    任何持有有效 JWT 的用户都可查询，但通常只有房内玩家会调。
    """
    registry = get_registry()
    room = registry.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="房间不存在")

    return ApiResponse(data=RoomInfoResponse(
        room_id=room.room_id,
        code=room.code,
        phase=room.phase.value,  # type: ignore[arg-type]
        players=[p.public_to_dict() for p in room.players],
        current_player_id=room.current_player.player_id if room.current_player else None,
        created_at=datetime.fromtimestamp(room.created_at, tz=timezone.utc),
    ).model_dump())


@router.delete("/{room_id}", response_model=ApiResponse)
async def destroy_room(room_id: str, uid: CurrentUser) -> ApiResponse:
    """销毁房间（仅限房内玩家）。"""
    registry = get_registry()
    room = registry.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="房间不存在")

    # 仅限房内玩家销毁
    if room.get_player(uid) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅限房内玩家销毁房间",
        )

    await registry.remove_room(room_id)
    return ApiResponse(msg="room destroyed")
