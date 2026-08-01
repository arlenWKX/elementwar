"""健康检查接口。"""

from __future__ import annotations

import time

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.chemkit_adapter import adapter as chemkit_adapter
from app.chemkit_adapter.cache import cache_stats
from app.config import settings
from app.connection.room_registry import get_registry
from app.models.cards import get_substance_registry
from app.models.db import get_session
from app.models.schemas import ApiResponse, HealthResponse

router = APIRouter(prefix="/health", tags=["health"])

_START_TIME = time.time()


@router.get("", response_model=ApiResponse)
@router.get("/", response_model=ApiResponse)
async def health() -> ApiResponse:
    """健康检查端点。

    返回服务版本、运行时长、chemkit 加载状态、数据库连接状态、活跃房间数。
    """
    db_ok = True
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    registry = get_registry()
    stats = registry.stats()

    return ApiResponse(data=HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        uptime_sec=time.time() - _START_TIME,
        chemkit_loaded=chemkit_adapter.is_loaded(),
        materials_loaded=get_substance_registry().loaded,
        db_connected=db_ok,
        active_rooms=stats["total_rooms"],
    ).model_dump())


@router.get("/detailed", response_model=ApiResponse)
async def detailed_health() -> ApiResponse:
    """详细健康信息（含缓存统计、物质注册表统计）。"""
    registry = get_registry()
    sub_registry = get_substance_registry()
    return ApiResponse(data={
        "version": __version__,
        "env": settings.env,
        "uptime_sec": time.time() - _START_TIME,
        "chemkit": {
            "loaded": chemkit_adapter.is_loaded(),
            "data_dir": settings.chemkit_data_dir,
            "cache": cache_stats(),
        },
        "materials": {
            "loaded": sub_registry.loaded,
            "substances": len(sub_registry.all_substances()),
            "conditions": len(sub_registry.conditions),
            "privileges": len(sub_registry.privilege_effects),
        },
        "rooms": registry.stats(),
    })
