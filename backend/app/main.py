"""FastAPI 主入口。

将 Socket.IO ASGI 应用挂载到 /socket.io，
FastAPI REST 路由挂载到 /api，
健康检查挂载到 /health，
Web 客户端静态文件挂载到 /web 和 /。

启动顺序：
1. ensure_dirs() 创建必要目录
2. setup_logging() 初始化日志（显式调用，无 import 副作用）
3. init_db() 创建数据库表
4. init_chemkit() 加载并预热 chemkit
5. 装配 ASGI 应用

关闭顺序：
1. dispose_db() 释放数据库连接
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import admin, auth, game, health, rooms
from app.chemkit_adapter.adapter import init_chemkit
from app.config import ensure_dirs, settings
from app.connection.socket_server import create_socketio_app
from app.models.cards import get_substance_registry
from app.models.db import dispose_db, init_db
from app.services.logger import get_logger, setup_logging

logger = get_logger(__name__)

# Web 客户端静态文件目录（项目根目录下的 frontend/web）
_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期。"""
    # Startup
    ensure_dirs()
    setup_logging()
    logger.info(
        "app.starting",
        extra={
            "env": settings.env,
            "version": __version__,
            "host": settings.host,
            "port": settings.port,
        },
    )
    await init_db()
    logger.info("db.initialized")

    # 加载物质注册表
    registry = get_substance_registry()
    if not registry.loaded:
        logger.warning("materials.not_loaded")
    else:
        logger.info(
            "materials.loaded",
            extra={"substances": len(registry.all_substances())},
        )

    if settings.chemkit_warmup_on_start:
        await init_chemkit()
        logger.info("chemkit.ready")

    # 启动后台任务：定期清理空闲房间
    cleanup_task = asyncio.create_task(_idle_cleanup_loop())

    logger.info("app.started")
    yield
    # Shutdown
    logger.info("app.stopping")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    await dispose_db()
    logger.info("app.stopped")


async def _idle_cleanup_loop() -> None:
    """每 60 秒清理一次空闲房间。"""
    from app.connection.room_registry import get_registry
    while True:
        await asyncio.sleep(60)
        try:
            registry = get_registry()
            await registry.cleanup_idle_rooms()
        except Exception as e:
            logger.warning("cleanup_loop.error", extra={"err": str(e)})


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    app = FastAPI(
        title="ElementWar Backend",
        description="《元素战争：临界反应》Python + FastAPI 后端",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST 路由
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(rooms.router)
    app.include_router(game.router)
    app.include_router(admin.router)

    # Socket.IO（挂载到 /socket.io）
    sio_app = create_socketio_app()
    app.mount("/socket.io", sio_app)

    # Web 客户端静态文件（挂载到 /web）
    if _WEB_DIR.exists():
        app.mount("/web", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
        logger.info("web.mounted", extra={"path": str(_WEB_DIR)})

        @app.get("/", include_in_schema=False)
        async def root_redirect():
            """根路径返回 web 客户端入口（避免 404）。"""
            index = _WEB_DIR / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return JSONResponse({"msg": "ElementWar Backend", "web": "/web/"})
    else:
        @app.get("/", include_in_schema=False)
        async def root_placeholder():
            return JSONResponse({
                "msg": "ElementWar Backend",
                "note": "Web client not built. Visit /api/docs for API.",
            })

    return app


# 全局 ASGI 应用实例（被 uvicorn 加载）
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_dev,
        log_level=settings.log_level.lower(),
    )
