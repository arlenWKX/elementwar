"""Socket.IO 服务器与事件路由。

职责：
- 验证 socket 对应玩家是否处于其轮次（转发给游戏逻辑层判定）
- 不实现具体规则，仅做路由
- 将逻辑层结果广播给房间内玩家
- AI 调度交由 ai_scheduler 独立模块处理（避免循环依赖）

事件路由表：
- connect: 校验 token 或注册新玩家
- react: 转发到 action_processor.process_react_action
- end_turn: 转发到 action_processor.end_action_active
- exchange: 转发到 reward.process_reward_exchange
- choose_product: 处理多产物选择
- disconnect: 标记离线 + 启动重连令牌倒计时
"""

from __future__ import annotations

import socketio

from app.connection import events as E
from app.config import get_game_config
from app.connection.reconnect import validate_and_consume_token
from app.connection.room_registry import get_registry
from app.core.action_processor import end_action_active, process_react_action
from app.core.game_state import GameRoom, RoomPhase
from app.core.reward import process_reward_exchange
from app.models.db import get_session
from app.models.schemas import ReactActionPayload, RewardExchangePayload
from app.services.logger import audit_event, get_logger

logger = get_logger(__name__)


# ============================================================
# Socket.IO 服务器（ASGI 模式，挂载到 FastAPI）
# ============================================================
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",  # 生产环境应在 Nginx 层控制
    ping_interval=20,
    ping_timeout=60,
    transports=["websocket"],
)


# ============================================================
# 连接管理
# ============================================================
@sio.event
async def connect(sid: str, environ: dict, auth: dict | None) -> bool:
    """新连接。

    auth 字段可携带：
    - {token: "..."} 断线重连
    - {uid: "...", room_id: "..."} JWT 已在 REST 层校验，此处仅校验 uid 存在并绑定房间
    """
    if auth is None:
        auth = {}
    logger.info("socket.connect", extra={"sid": sid, "auth_keys": list(auth.keys())})

    # 1. token 重连
    token = auth.get("token")
    if token:
        async with get_session() as session:
            result = await validate_and_consume_token(session, token)
        if result is None:
            logger.warning("socket.invalid_token", extra={"sid": sid})
            await sio.emit(E.EVT_ERROR, {"code": "invalid_token", "msg": "令牌无效或已过期"}, to=sid)
            return False
        room_id, player_id = result
        registry = get_registry()
        room = registry.get_room(room_id)
        if room is None:
            await sio.emit(E.EVT_ERROR, {"code": "room_gone", "msg": "房间已不存在"}, to=sid)
            return False
        registry.bind_sid(room_id, player_id, sid)
        await sio.enter_room(sid, f"room:{room_id}")
        await sio.emit(
            E.EVT_PLAYER_RECONNECTED,
            {"player_id": player_id, "room_id": room_id},
            to=sid,
        )
        await sio.emit(E.EVT_STATE_SYNC, room.snapshot_for_player(player_id), to=sid)
        logger.info("socket.reconnected", extra={"sid": sid, "room_id": room_id, "player_id": player_id})
        return True

    # 2. uid + room_id 直接连接（JWT 已在 REST 层校验过）
    uid = auth.get("uid")
    room_id = auth.get("room_id")
    if uid and room_id:
        registry = get_registry()
        room = registry.get_room(room_id)
        if room is None:
            await sio.emit(E.EVT_ERROR, {"code": "room_gone", "msg": "房间已不存在"}, to=sid)
            return False
        # 玩家必须在房间内
        player = room.get_player(uid)
        if player is None:
            await sio.emit(E.EVT_ERROR, {"code": "not_in_room", "msg": "未加入房间"}, to=sid)
            return False
        registry.bind_sid(room_id, uid, sid)
        await sio.enter_room(sid, f"room:{room_id}")
        # 给新连接的玩家发送私有状态
        await sio.emit(E.EVT_STATE_SYNC, room.snapshot_for_player(uid), to=sid)
        # 广播给房间内所有在线玩家：有人加入/重连，触发状态刷新
        room._enqueue_broadcast("player:joined", {
            "player_id": uid, "name": player.name,
        })
        await flush_events(room)
        return True

    # 3. 匿名连接（仅注册 sid，等客户端调用 join）
    return True


@sio.event
async def disconnect(sid: str) -> None:
    """断开连接：解绑 sid，标记离线。"""
    registry = get_registry()
    result = registry.unbind_sid(sid)
    if result is not None:
        room_id, player_id = result
        room = registry.get_room(room_id)
        if room is not None:
            room._enqueue_broadcast(E.EVT_PLAYER_LEFT, {
                "player_id": player_id, "sid": sid, "reason": "disconnect",
            })
            await flush_events(room)
        audit_event(room_id, "socket.disconnect", player_id=player_id, sid=sid)
    logger.info("socket.disconnect", extra={"sid": sid})


# ============================================================
# 玩家行动事件
# ============================================================
@sio.on(E.EVT_REACT)
async def on_react(sid: str, data: dict) -> None:
    """接龙：打出物质牌 + 条件牌 + 特权卡。"""
    room, player_id = await _resolve(sid)
    if room is None:
        return
    try:
        payload = ReactActionPayload.model_validate(data)
    except Exception as e:
        await sio.emit(E.EVT_ERROR, {"code": "invalid_payload", "msg": str(e)}, to=sid)
        return

    await process_react_action(room, player_id, payload)
    registry = get_registry()
    registry.touch(room.room_id)
    await flush_events(room)


@sio.on(E.EVT_END_TURN)
async def on_end_turn(sid: str, data: dict | None = None) -> None:
    """主动结束回合。"""
    room, player_id = await _resolve(sid)
    if room is None:
        return
    end_action_active(room, player_id)
    registry = get_registry()
    registry.touch(room.room_id)
    await flush_events(room)


@sio.on(E.EVT_EXCHANGE)
async def on_exchange(sid: str, data: dict) -> None:
    """奖励分兑换。"""
    room, player_id = await _resolve(sid)
    if room is None:
        return
    try:
        payload = RewardExchangePayload.model_validate(data)
    except Exception as e:
        await sio.emit(E.EVT_ERROR, {"code": "invalid_payload", "msg": str(e)}, to=sid)
        return
    process_reward_exchange(room, player_id, payload)
    await flush_events(room)


@sio.on(E.EVT_CHOOSE_PRODUCT)
async def on_choose_product(sid: str, data: dict) -> None:
    """玩家选择产物（当反应产生多个新产物时）。

    委托给 action_processor.apply_chosen_product 处理，校验由 apply_chosen_product 内部完成。
    """
    room, player_id = await _resolve(sid)
    if room is None:
        return

    chosen = data.get("product")
    from app.core.action_processor import apply_chosen_product
    apply_chosen_product(room, player_id, chosen)
    await flush_events(room)


@sio.on(E.EVT_EXTRACT)
async def on_extract(sid: str, data: dict) -> None:
    """特权卡：萃取（从弃牌堆选 1 张物质牌加入手牌）。"""
    room, player_id = await _resolve(sid)
    if room is None:
        return
    try:
        from app.models.schemas import ExtractPayload
        payload = ExtractPayload.model_validate(data)
    except Exception as e:
        await sio.emit(E.EVT_ERROR, {"code": "invalid_payload", "msg": str(e)}, to=sid)
        return
    from app.core.privilege_ops import process_extract
    process_extract(room, player_id, payload.target_card_id, payload.privilege_card_id)
    await flush_events(room)


@sio.on(E.EVT_DISTILL)
async def on_distill(sid: str, data: dict) -> None:
    """特权卡：蒸馏（查看牌库顶 3 张，选 1 张加入手牌）。"""
    room, player_id = await _resolve(sid)
    if room is None:
        return
    try:
        from app.models.schemas import DistillPayload
        payload = DistillPayload.model_validate(data)
    except Exception as e:
        await sio.emit(E.EVT_ERROR, {"code": "invalid_payload", "msg": str(e)}, to=sid)
        return
    from app.core.privilege_ops import process_distill
    process_distill(room, player_id, payload.privilege_card_id, payload.chosen_index)
    await flush_events(room)


@sio.on(E.EVT_END_ACTION)
async def on_end_action(sid: str, data: dict | None = None) -> None:
    """主动结束本次行动（停止连锁）。"""
    room, player_id = await _resolve(sid)
    if room is None:
        return
    player = room.get_player(player_id)
    if player is None or room.current_player != player:
        await sio.emit(E.EVT_ERROR, {"code": "not_your_turn", "msg": "非你的轮次"}, to=sid)
        return
    from app.core.action_processor import end_action
    end_action(room, player)
    await flush_events(room)


@sio.on(E.EVT_READY)
async def on_ready(sid: str, data: dict | None = None) -> None:
    """玩家准备开始。

    全员 ready 后才开始游戏：
    - 每个 online 玩家都需要主动调用 ready
    - AI 玩家在加入时自动 ready
    - 全员 ready + 人数 ≥ min_players → 触发 start_game
    """
    cfg = get_game_config()
    registry = get_registry()
    result = registry.find_room_by_sid(sid)
    if result is None:
        return
    room, player_id = result

    if len(room.players) < cfg.room_min_players:
        await sio.emit(
            E.EVT_ERROR,
            {"code": "not_enough_players", "msg": f"至少需要 {cfg.room_min_players} 人才能开始"},
            to=sid,
        )
        return
    if room.phase != RoomPhase.WAITING:
        return

    all_ready = room.mark_ready(player_id)
    # 入队 ready_changed 事件，由 flush_events 统一发送，
    # 同时为每个在线玩家补发含私有状态的 state:sync
    from app.connection.events import EVT_READY_CHANGED
    room._enqueue_broadcast(EVT_READY_CHANGED, {
        "ready_player_id": player_id,
        "all_ready": all_ready,
        "players": [p.public_to_dict() for p in room.players],
    })

    if not all_ready:
        await flush_events(room)
        return

    room.start_game()
    await flush_events(room)


@sio.on(E.EVT_LEAVE)
async def on_leave(sid: str, data: dict | None = None) -> None:
    """玩家主动离开房间。"""
    registry = get_registry()
    result = registry.unbind_sid(sid)
    if result is None:
        return
    room_id, player_id = result
    room = registry.get_room(room_id)
    if room is not None:
        room._enqueue_broadcast(E.EVT_PLAYER_LEFT, {
            "player_id": player_id, "sid": sid, "reason": "leave",
        })
        await flush_events(room)
    audit_event(room_id, "player.left", player_id=player_id, sid=sid)


# ============================================================
# 内部辅助
# ============================================================
async def _resolve(sid: str) -> tuple[GameRoom, str] | tuple[None, None]:
    """从 sid 解析 (room, player_id)。

    若未加入房间，向客户端发送 not_in_room 错误并返回 (None, None)。
    """
    registry = get_registry()
    result = registry.find_room_by_sid(sid)
    if result is None:
        await sio.emit(E.EVT_ERROR, {"code": "not_in_room", "msg": "未加入房间"}, to=sid)
        return None, None
    return result


async def flush_events(room: GameRoom) -> None:
    """把房间待广播事件队列中的事件全部发送，并给每个在线玩家推送私有状态。

    每次调用后，所有在线玩家都会收到含自己手牌的 state:sync。
    """
    events = room.drain_events()
    room_tag = f"room:{room.room_id}"

    # 1. 发送队列中的事件（广播 + 定向）
    for evt in events:
        try:
            if evt.target_player_id is None:
                await sio.emit(evt.event, evt.data, room=room_tag)
            else:
                target = room.get_player(evt.target_player_id)
                if target and target.sid:
                    await sio.emit(evt.event, evt.data, to=target.sid)
        except Exception as e:
            logger.error(
                "socket.emit_failed",
                extra={"event": evt.event, "err": str(e)},
            )

    # 2. 给每个在线玩家推送含私有信息的 state:sync
    for player in room.players:
        if player.sid:
            try:
                await sio.emit(
                    E.EVT_STATE_SYNC,
                    room.snapshot_for_player(player.player_id),
                    to=player.sid,
                )
            except Exception as e:
                logger.error(
                    "socket.sync_failed",
                    extra={"player": player.player_id, "err": str(e)},
                )


# ============================================================
# ASGI 应用
# ============================================================
def create_socketio_app() -> socketio.ASGIApp:
    """构造 Socket.IO ASGI 应用。

    由 FastAPI main.py 挂载到 /socket.io 路径。
    """
    return socketio.ASGIApp(sio, socketio_path="socket.io")


__all__ = ["sio", "create_socketio_app", "flush_events"]
