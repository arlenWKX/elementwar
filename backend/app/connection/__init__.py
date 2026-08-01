"""连接与房间层。

按架构设计：
- 知道"哪个 socket 属于哪个玩家、哪个房间"
- 不知道"这张牌能不能打、反应产物是什么"
- 转发原始事件给游戏逻辑层，并广播逻辑层结果

模块：
- events.py: 事件名常量
- room_registry.py: 全局房间集合
- socket_server.py: Socket.IO ASGI 应用与事件路由
- reconnect.py: 断线重连 token 发放与校验
"""

from __future__ import annotations

from app.connection import events
from app.connection.events import *  # noqa: F401,F403
