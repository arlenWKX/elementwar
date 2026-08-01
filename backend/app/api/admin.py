"""管理接口。

提供：
- GET /api/admin/rooms: 列出所有房间
- POST /api/admin/cleanup: 清理空闲房间
- GET /api/admin/cache: 查看缓存统计
- GET /api/admin/health/detailed: 详细健康信息

注：反应预览已移至 /api/game/reactions:preview（公开接口）。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.chemkit_adapter.cache import cache_stats
from app.connection.room_registry import get_registry
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/rooms", response_model=ApiResponse)
async def list_rooms() -> ApiResponse:
    """列出所有房间（概要）。"""
    registry = get_registry()
    rooms = []
    for room in registry.iter_rooms():
        rooms.append({
            "room_id": room.room_id,
            "code": room.code,
            "phase": room.phase.value,
            "players": [p.public_to_dict() for p in room.players],
            "current_player_id": room.current_player.player_id if room.current_player else None,
            "turn_no": room.turn_no,
            "round_no": room.round_no,
            "vs_ai": room.vs_ai,
        })
    return ApiResponse(data={"rooms": rooms, "stats": registry.stats()})


@router.post("/cleanup", response_model=ApiResponse)
async def cleanup_rooms() -> ApiResponse:
    """清理空闲房间。"""
    registry = get_registry()
    removed = await registry.cleanup_idle_rooms()
    return ApiResponse(data={"removed": removed})


@router.get("/cache", response_model=ApiResponse)
async def cache_info() -> ApiResponse:
    """缓存统计。"""
    return ApiResponse(data=cache_stats())
