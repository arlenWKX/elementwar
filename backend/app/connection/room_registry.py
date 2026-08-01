"""房间注册表。

维护全局所有活跃房间，提供：
- 创建房间（生成 ID + 匹配码）
- 通过匹配码加入房间
- 通过房间 ID / 玩家 ID / Socket.IO sid 查找房间
- 房间空闲超时回收
- 房间状态快照持久化

设计：内存为主，SQLite 仅用于审计/恢复。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Iterator

from app.config import get_game_config
from app.connection.reconnect import issue_token
from app.core.game_state import GameRoom, RoomPhase
from app.models.db import SessionToken, get_session, new_room_id
from app.services.logger import audit_event, get_logger

logger = get_logger(__name__)


def _gen_code(length: int | None = None) -> str:
    """生成匹配码：6 位数字（与 UID 同字符表，便于记忆与录入）。"""
    cfg = get_game_config()
    n = length or cfg.room_code_length
    return "".join(random.choices(cfg.uid_alphabet, k=n))


class RoomRegistry:
    """房间注册表单例。

    维护 in-memory 房间字典与 sid→player_id→room_id 反向索引。
    所有方法都是 async，因为涉及 SQLite 重连 token 读写。
    """

    def __init__(self) -> None:
        self._rooms: dict[str, GameRoom] = {}
        self._sid_index: dict[str, tuple[str, str]] = {}
        self._code_index: dict[str, str] = {}
        self._last_active: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # --------------------------------------------------------
    # 创建/加入
    # --------------------------------------------------------
    async def create_room(
        self,
        player_id: str,
        player_name: str,
        *,
        vs_ai: bool = False,
        max_players: int | None = None,
    ) -> tuple[GameRoom, str]:
        """创建房间。

        Returns:
            (room, reconnect_token)
        """
        async with self._lock:
            room_id = new_room_id()
            for _ in range(10):
                code = _gen_code()
                if code not in self._code_index:
                    break
            else:
                raise RuntimeError("匹配码生成失败")

            room = GameRoom(
                room_id=room_id, code=code, vs_ai=vs_ai,
                max_players=max_players if max_players is not None else get_game_config().room_max_players,
            )
            room.add_player(player_id, player_name, is_ai=False)
            self._rooms[room_id] = room
            self._code_index[code] = room_id
            self._last_active[room_id] = time.time()

        audit_event(room_id, "room.created", code=code, player_id=player_id, vs_ai=vs_ai)
        token = await self._issue_token(room_id, player_id)
        return room, token

    async def join_room(
        self,
        code: str,
        player_id: str,
        player_name: str,
    ) -> tuple[GameRoom, str] | None:
        """按匹配码加入房间。

        Returns:
            (room, reconnect_token) 或 None（房间不存在或已满）
        """
        async with self._lock:
            room_id = self._code_index.get(code.upper())
            if room_id is None:
                return None
            room = self._rooms.get(room_id)
            if room is None:
                return None
            if room.is_full:
                return None
            room.add_player(player_id, player_name, is_ai=False)
            self._last_active[room_id] = time.time()

        audit_event(room_id, "player.joined", player_id=player_id, code=code)
        token = await self._issue_token(room_id, player_id)
        return room, token

    # --------------------------------------------------------
    # 查找
    # --------------------------------------------------------
    def get_room(self, room_id: str) -> GameRoom | None:
        return self._rooms.get(room_id)

    def find_room_by_sid(self, sid: str) -> tuple[GameRoom, str] | None:
        """通过 Socket.IO sid 查找房间与玩家 ID。"""
        idx = self._sid_index.get(sid)
        if idx is None:
            return None
        room_id, player_id = idx
        room = self._rooms.get(room_id)
        if room is None:
            return None
        return room, player_id

    def iter_rooms(self) -> Iterator[GameRoom]:
        """公开迭代器：迭代所有房间（替代直接访问 _rooms）。"""
        return iter(self._rooms.values())

    # --------------------------------------------------------
    # 在线状态
    # --------------------------------------------------------
    def bind_sid(self, room_id: str, player_id: str, sid: str) -> None:
        """绑定 Socket.IO sid 与玩家。"""
        self._sid_index[sid] = (room_id, player_id)
        room = self._rooms.get(room_id)
        if room is not None:
            player = room.get_player(player_id)
            if player is not None:
                player.sid = sid
                player.online = True

    def unbind_sid(self, sid: str) -> tuple[str, str] | None:
        """解绑 sid。返回 (room_id, player_id)。"""
        idx = self._sid_index.pop(sid, None)
        if idx is None:
            return None
        room_id, player_id = idx
        room = self._rooms.get(room_id)
        if room is not None:
            player = room.get_player(player_id)
            if player is not None:
                player.online = False
                player.sid = None
        return idx

    def touch(self, room_id: str) -> None:
        """更新房间最后活跃时间。"""
        self._last_active[room_id] = time.time()

    # --------------------------------------------------------
    # 移除/回收
    # --------------------------------------------------------
    async def remove_room(self, room_id: str) -> None:
        """移除房间。"""
        async with self._lock:
            room = self._rooms.pop(room_id, None)
            self._last_active.pop(room_id, None)
            if room is not None:
                self._code_index.pop(room.code.upper(), None)
                sids_to_remove = [
                    sid for sid, (rid, _) in self._sid_index.items()
                    if rid == room_id
                ]
                for sid in sids_to_remove:
                    self._sid_index.pop(sid, None)
        audit_event(room_id, "room.removed")

    async def cleanup_idle_rooms(self) -> int:
        """清理空闲超时的房间。

        Returns:
            被清理的房间数
        """
        cfg = get_game_config()
        now = time.time()
        timeout = cfg.room_idle_timeout_sec
        to_remove: list[str] = []
        for room_id, last_active in self._last_active.items():
            if now - last_active > timeout:
                room = self._rooms.get(room_id)
                if room is None or room.phase == RoomPhase.WAITING or room.phase == RoomPhase.FINISHED:
                    to_remove.append(room_id)
        for rid in to_remove:
            await self.remove_room(rid)
        if to_remove:
            logger.info("room.cleanup", extra={"removed": len(to_remove), "remaining": len(self._rooms)})
        return len(to_remove)

    # --------------------------------------------------------
    # 内部：下发重连 token
    # --------------------------------------------------------
    async def _issue_token(self, room_id: str, player_id: str) -> str:
        """通过数据库 session 下发重连 token。"""
        async with get_session() as session:
            return await issue_token(session, room_id, player_id)

    # --------------------------------------------------------
    # 统计
    # --------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        return {
            "total_rooms": len(self._rooms),
            "waiting": sum(1 for r in self._rooms.values() if r.phase == RoomPhase.WAITING),
            "playing": sum(1 for r in self._rooms.values() if r.phase == RoomPhase.PLAYING),
            "finished": sum(1 for r in self._rooms.values() if r.phase == RoomPhase.FINISHED),
            "total_sids": len(self._sid_index),
        }


# 全局单例
_registry: RoomRegistry | None = None


def get_registry() -> RoomRegistry:
    """获取房间注册表单例。"""
    global _registry
    if _registry is None:
        _registry = RoomRegistry()
    return _registry


__all__ = ["RoomRegistry", "get_registry"]
