"""GameRoom 状态机核心。

承载一局游戏的全部权威状态，是连接层与游戏逻辑层之间的桥梁。
所有状态变更必须通过 GameRoom 的方法，以保证：
1. 校验合法性
2. 触发快照持久化
3. 生成广播事件

不直接调用网络层（不发送 Socket.IO 事件），由调用方读取 pending_events 后自行广播。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.connection.events import (
    EVT_ACTION_STARTED,
    EVT_CARDS_DECK_ADDED,
    EVT_CARDS_DRAWN,
    EVT_ERROR,
    EVT_GAME_STARTED,
    EVT_TURN_ENDED,
    EVT_TURN_STARTED
)
from app.config import get_game_config
from app.models.cards import (
    Card,
    CardType,
    PrivilegeEffect,
    build_card_pool,
)
from app.core.player_state import PlayerState
from app.services.logger import audit_event, get_logger

logger = get_logger(__name__)


class RoomPhase(str, Enum):
    """房间阶段。"""

    WAITING = "waiting"        # 等待玩家加入
    PLAYING = "playing"        # 游戏进行中
    FINISHED = "finished"      # 游戏结束


class TurnPhase(str, Enum):
    """轮次内行动阶段（玩家行动时的细分阶段）。"""

    IDLE = "idle"                       # 等待玩家行动
    AWAITING_PRODUCT_CHOICE = "awaiting_product"  # 反应成功，等待玩家选产物
    AWAITING_REWARD_USE = "awaiting_reward"      # 等待玩家使用奖励分
    ENDED = "ended"                     # 本玩家行动结束


@dataclass(slots=True)
class PendingEvent:
    """待广播的事件。"""

    event: str
    data: dict[str, Any]
    # 推送目标：None 表示广播给房间内所有人；player_id 表示仅推送给该玩家
    target_player_id: str | None = None


@dataclass(slots=True)
class GameRoom:
    """游戏房间。

    持有：
    - 房间元数据（ID、匹配码、创建时间）
    - 玩家列表（最多 game_config.room_max_players）
    - 游戏牌池（公共抽牌堆）
    - 场上物质
    - 当前回合/轮次/行动序号
    - 行动内临时状态（连锁反应历史、已用条件牌等）
    - 待广播事件队列
    """

    room_id: str
    code: str                              # 6 位匹配码
    created_at: float = field(default_factory=time.time)
    phase: RoomPhase = RoomPhase.WAITING
    max_players: int = field(default_factory=lambda: get_game_config().room_max_players)
    vs_ai: bool = False

    # 玩家
    players: list[PlayerState] = field(default_factory=list)

    # 牌池
    card_pool: list[Card] = field(default_factory=list)   # 公共牌池

    # 场上物质
    field_substance: str | None = None       # 物质名（如 "H_2O"）
    field_substance_mol: float = 1.0

    # 回合/轮次
    turn_no: int = 0                          # 当前回合号（1-based）
    round_no: int = 0                         # 当前轮次号（每回合从 1 开始）
    action_seq: int = 0                       # 本回合全局行动序号（用于排序）
    current_player_index: int = 0             # 当前行动玩家在 players 中的下标
    action_order: list[int] = field(default_factory=list)  # 本回合行动顺序（player_index 列表）

    # 行动内临时状态
    turn_phase: TurnPhase = TurnPhase.IDLE
    chain_step: int = 0                       # 当前连锁反应的第几步
    chain_history: list[dict] = field(default_factory=list)  # 本次行动的反应历史
    played_cards_this_action: list[Card] = field(default_factory=list)  # 本次行动已打出的牌（用于奖励分兑换-回收）
    pending_products: list[str] = field(default_factory=list)  # 等待玩家选择的产物列表
    pending_reaction_result: dict | None = None    # 上次反应结果（缓存给客户端）
    attempted_reaction: bool = False          # 本次行动是否尝试过反应（用于判定主动/被动结束）

    # 事件队列
    pending_events: list[PendingEvent] = field(default_factory=list)

    # 胜者
    winner_id: str | None = None

    # 修订号（每次状态变更 +1，用于增量同步）
    revision: int = 0

    # --------------------------------------------------------
    # 玩家管理
    # --------------------------------------------------------
    def add_player(self, player_id: str, name: str, *, is_ai: bool = False) -> PlayerState:
        """加入房间。

        AI 玩家自动 ready。
        """
        if len(self.players) >= self.max_players:
            raise ValueError("房间已满")
        # 重名检测
        existing = next((p for p in self.players if p.player_id == player_id), None)
        if existing is not None:
            return existing
        player = PlayerState(
            player_id=player_id,
            name=name,
            is_ai=is_ai,
            # AI 自动 ready
            ready=is_ai,
        )
        self.players.append(player)
        audit_event(self.room_id, "player.joined", player_id=player_id, name=name, is_ai=is_ai)
        return player

    def get_player(self, player_id: str) -> PlayerState | None:
        """按 ID 查找玩家。"""
        return next((p for p in self.players if p.player_id == player_id), None)

    @property
    def is_full(self) -> bool:
        return len(self.players) >= self.max_players

    @property
    def is_all_ready(self) -> bool:
        """全员都已 ready（在线玩家与 AI 自动 ready）。"""
        if not self.players:
            return False
        return all(p.ready for p in self.players)

    def mark_ready(self, player_id: str) -> bool:
        """标记玩家 ready。

        Returns:
            True 如果已全员 ready
        """
        player = self.get_player(player_id)
        if player is None:
            return False
        player.ready = True
        return self.is_all_ready

    @property
    def current_player(self) -> PlayerState | None:
        if self.phase != RoomPhase.PLAYING:
            return None
        if not self.players or self.current_player_index >= len(self.players):
            return None
        return self.players[self.current_player_index]

    # --------------------------------------------------------
    # 游戏开始
    # --------------------------------------------------------
    def start_game(self) -> None:
        """开始游戏：洗牌、发牌、初始化回合。

        支持 2-3 玩家。要求达到 min_players 才能开始。
        """
        cfg = get_game_config()
        if len(self.players) < cfg.room_min_players:
            raise ValueError(f"玩家不足，至少需要 {cfg.room_min_players} 人")
        if len(self.players) > cfg.room_max_players:
            raise ValueError(f"玩家过多，最多 {cfg.room_max_players} 人")
        if self.phase != RoomPhase.WAITING:
            raise ValueError(f"非法阶段: {self.phase}")

        self.phase = RoomPhase.PLAYING
        self.turn_no = 1

        # 构建牌池（按玩家人数）
        self.card_pool = build_card_pool(num_players=len(self.players))
        random.shuffle(self.card_pool)

        # 每人牌库与初始手牌
        for player in self.players:
            for _ in range(cfg.deck_size_init):
                if self.card_pool:
                    player.deck.append(self.card_pool.pop())
            random.shuffle(player.deck)
            player.draw_from_deck(cfg.hand_size_init)

        # 第一回合行动顺序：按加入顺序
        self.action_order = list(range(len(self.players)))
        self.current_player_index = 0
        self.round_no = 1
        self.action_seq = 0

        # 回合启动补充：每人抽 1 张到牌库（开局时已发 30 张，此处省略）
        # 留作后续回合实现

        audit_event(self.room_id, "game.started", players=[p.player_id for p in self.players])
        self._enqueue_broadcast(EVT_GAME_STARTED, self._build_state_snapshot())
        self._enqueue_broadcast(EVT_TURN_STARTED, {
            "turn_no": self.turn_no,
            "round_no": self.round_no,
            "current_player_id": self.current_player.player_id if self.current_player else None,
        })

    # --------------------------------------------------------
    # 行动顺序计算（规则文件四.1）
    # --------------------------------------------------------
    def compute_next_turn_order(self) -> list[int]:
        """按上一回合数据计算本回合行动顺序。

        第一层：上一回合主动结束的玩家，按主动结束先后顺序倒序排列。
        第二层：上一回合未主动/被动结束，至少成功行动 1 次的玩家，
                按最后一次成功行动先后顺序正序排列。
        第三层：上一回合被动结束的玩家，按被动结束先后顺序正序排列。
        """
        active_ended: list[tuple[int, int]] = []   # (player_index, order)
        acted: list[tuple[int, int]] = []
        passive_ended: list[tuple[int, int]] = []

        for i, p in enumerate(self.players):
            if p.ended_round_active:
                active_ended.append((i, p.last_active_end_order))
            elif p.ended_round and not p.ended_round_active:
                passive_ended.append((i, p.last_passive_end_order))
            elif p.last_action_order > 0:
                acted.append((i, p.last_action_order))

        # 第一层倒序
        active_ended.sort(key=lambda x: x[1], reverse=True)
        # 第二、三层正序
        acted.sort(key=lambda x: x[1])
        passive_ended.sort(key=lambda x: x[1])

        order = [i for i, _ in active_ended] + [i for i, _ in acted] + [i for i, _ in passive_ended]
        # 兜底：未在以上分类的玩家排到最后
        classified = set(order)
        for i in range(len(self.players)):
            if i not in classified:
                order.append(i)
        return order

    # --------------------------------------------------------
    # 回合/轮次推进
    # --------------------------------------------------------
    def advance_to_next_player(self) -> bool:
        """轮次推进：到下一个未结束回合的玩家。

        Returns:
            True 如果还有玩家可行动；False 如果本回合所有玩家都已结束。
        """
        for offset in range(1, len(self.players) + 1):
            next_idx = (self.current_player_index + offset) % len(self.players)
            if not self.players[next_idx].ended_round:
                self.current_player_index = next_idx
                self.round_no += 1
                return True
        # 所有玩家都已结束
        return False

    def end_turn(self) -> None:
        """回合结束：清零奖励分、回收弃牌堆、推进到下一回合。

        按规则 §四.3 + §三（回合启动补充）：
        - 各玩家奖励分清零
        - 各玩家弃牌堆中的牌全部返回游戏牌池
        - 场上物质若仍存在（未被覆盖），返回游戏牌池
        - 进入下一回合
        - 回合启动：每人从游戏牌池抽 1 张加入个人牌库
        """
        # 先计算下一回合行动顺序（此时 ended_round_active 等标记还在）
        new_action_order = self.compute_next_turn_order()

        # 各玩家弃牌堆回收（牌返回游戏牌池）
        for player in self.players:
            for card in player.discard:
                self.card_pool.append(card)
            player.discard.clear()
            player.reward_points = 0
            player.start_new_round()  # 重置回合内标记（保留 consecutive_active_ends）

        # 场上物质若仍有对应 Card 实例（初始物质未被覆盖），返回游戏牌池
        # 规则§二：物质牌最终会返回游戏牌池
        if self.field_substance is not None:
            # 检查 played_cards_this_action 中是否有匹配的初始物质牌
            initial_card = next(
                (c for c in self.played_cards_this_action
                 if c.type == CardType.SUBSTANCE and c.name == self.field_substance
                 and not c.frozen),
                None,
            )
            if initial_card is not None:
                self.played_cards_this_action.remove(initial_card)
                self.card_pool.append(initial_card)
            # 否则场上物质是反应产物（无 Card 实例），无需回收

        self.turn_no += 1
        self.round_no = 1
        self.action_seq = 0
        self.field_substance = None
        self.field_substance_mol = 0.0
        self.chain_step = 0
        self.chain_history.clear()
        self.played_cards_this_action.clear()
        self.pending_products.clear()
        self.pending_reaction_result = None
        self.attempted_reaction = False
        self.turn_phase = TurnPhase.IDLE

        # 应用新行动顺序
        self.action_order = new_action_order
        # current_player_index 重置为 action_order[0]
        self.current_player_index = self.action_order[0] if self.action_order else 0

        # 回合启动补充：每人从游戏牌池抽 1 张加入个人牌库
        for player in self.players:
            if self.card_pool:
                drawn = self.card_pool.pop()
                player.deck.append(drawn)
                self._enqueue_to_player(
                    player.player_id, EVT_CARDS_DECK_ADDED,
                    {"card_type": drawn.type.value, "source": "turn_start"},
                )

        audit_event(self.room_id, "turn.ended", turn_no=self.turn_no - 1)
        self._enqueue_broadcast(EVT_TURN_ENDED, {"turn_no": self.turn_no - 1})
        self._enqueue_broadcast(EVT_TURN_STARTED, {
            "turn_no": self.turn_no,
            "round_no": self.round_no,
            "current_player_id": self.current_player.player_id if self.current_player else None,
        })

    # --------------------------------------------------------
    # 行动开始
    # --------------------------------------------------------
    def start_action(self, player_id: str) -> None:
        """开始一个玩家的行动。

        - 行动低保：手牌 ≤ 2 时抽到 8 张
        - 重置行动内临时状态
        """
        player = self.get_player(player_id)
        if player is None:
            raise ValueError("玩家不存在")
        if player.ended_round:
            raise ValueError("该玩家本回合已结束")
        if self.current_player != player:
            raise ValueError("非该玩家轮次")

        # 行动低保：手牌 ≤ 阈值时抽到目标张数
        cfg = get_game_config()
        if len(player.hand) <= cfg.low_security_threshold:
            drawn = player.draw_to_hand_limit(target=cfg.low_security_draw_to)
            if drawn:
                self._enqueue_to_player(player_id, EVT_CARDS_DRAWN, {
                    "cards": [c.to_dict() for c in drawn],
                    "source": "low_security",
                })

        # 重置行动内状态
        # 注意：played_cards_this_action 由 _finalize_action 在上一行动结束时清理，
        # 此处不再清空（避免清空未回收的初始物质）
        self.chain_step = 0
        self.chain_history.clear()
        self.pending_products.clear()
        self.pending_reaction_result = None
        self.attempted_reaction = False
        self.turn_phase = TurnPhase.IDLE

        self.action_seq += 1

        audit_event(self.room_id, "action.started", player_id=player_id, action_seq=self.action_seq)
        self._enqueue_to_player(player_id, EVT_ACTION_STARTED, {"action_seq": self.action_seq})

    # --------------------------------------------------------
    # 事件队列
    # --------------------------------------------------------
    def _enqueue_broadcast(self, event: str, data: dict[str, Any]) -> None:
        """加入广播事件。"""
        self.pending_events.append(PendingEvent(event=event, data=data, target_player_id=None))
        self.revision += 1

    def _enqueue_to_player(self, player_id: str, event: str, data: dict[str, Any]) -> None:
        """加入定向事件。"""
        self.pending_events.append(PendingEvent(event=event, data=data, target_player_id=player_id))

    def _enqueue_error(self, player_id: str, code: str, msg: str) -> None:
        """加入错误事件。"""
        self._enqueue_to_player(player_id, EVT_ERROR, {"code": code, "msg": msg})

    def drain_events(self) -> list[PendingEvent]:
        """取出并清空事件队列。"""
        events = self.pending_events
        self.pending_events = []
        return events

    # --------------------------------------------------------
    # 状态快照
    # --------------------------------------------------------
    def _build_state_snapshot(self) -> dict[str, Any]:
        """构建状态快照（公开信息）。"""
        return {
            "room_id": self.room_id,
            "phase": self.phase.value,
            "turn_no": self.turn_no,
            "round_no": self.round_no,
            "action_seq": self.action_seq,
            "current_player_id": self.current_player.player_id if self.current_player else None,
            "field_substance": self.field_substance,
            "field_substance_mol": self.field_substance_mol,
            "turn_phase": self.turn_phase.value,
            "chain_step": self.chain_step,
            "players": [p.public_to_dict() for p in self.players],
            "winner_id": self.winner_id,
            "revision": self.revision,
        }

    def snapshot_for_player(self, player_id: str) -> dict[str, Any]:
        """构建包含玩家私有信息的状态快照。"""
        snap = self._build_state_snapshot()
        player = self.get_player(player_id)
        if player is not None:
            snap["my_hand"] = player.hand_to_dict()
            snap["my_reward_points"] = player.reward_points
        return snap


__all__ = ["GameRoom", "RoomPhase", "TurnPhase", "PendingEvent"]
