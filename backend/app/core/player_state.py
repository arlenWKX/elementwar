"""玩家状态。

承载玩家在游戏中的全部私有状态：
- 牌库 (deck)：30 张抽牌堆
- 手牌 (hand)：上限 10 张（行动中可超，行动结束时刻自选弃至 10）
- 弃牌堆 (discard)：本回合覆盖的旧物质、已使用的条件牌（冻结至回合结束）
- 奖励分 (reward_points)：仅在自己行动中使用，回合结束时清零
- 回合内行动标记
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_game_config
from app.models.cards import Card, CardType, PrivilegeEffect


@dataclass(slots=True)
class PlayerState:
    """玩家状态。"""

    player_id: str
    name: str
    # 牌区
    deck: list[Card] = field(default_factory=list)            # 个人牌库（抽牌堆）
    hand: list[Card] = field(default_factory=list)            # 手牌
    discard: list[Card] = field(default_factory=list)          # 弃牌堆（本回合覆盖的旧物质+条件牌）
    # 奖励分
    reward_points: int = 0
    # 回合内状态
    ended_round: bool = False           # 本回合是否已结束（主动或被动）
    ended_round_active: bool = False    # 本回合是否主动结束
    consecutive_active_ends: int = 0    # 连续主动结束次数（不能连续 2 回合）
    last_action_at_turn: int = -1        # 上一次成功行动的轮次号
    last_action_order: int = 0           # 本回合最后一次成功行动的全局序号
    last_active_end_order: int = 0       # 本回合主动结束的全局序号
    last_passive_end_order: int = 0      # 本回合被动结束的全局序号
    # AI 标记
    is_ai: bool = False
    # 在线状态
    online: bool = True
    sid: str | None = None              # Socket.IO session id
    # 准备状态
    ready: bool = False

    # --------------------------------------------------------
    # 牌区操作
    # --------------------------------------------------------
    def draw_from_deck(self, n: int = 1) -> list[Card]:
        """从牌库顶抽 n 张牌到手牌。"""
        drawn: list[Card] = []
        for _ in range(n):
            if not self.deck:
                break
            card = self.deck.pop(0)
            self.hand.append(card)
            drawn.append(card)
        return drawn

    def draw_to_hand_limit(self, target: int = 8) -> list[Card]:
        """行动低保：抽到手牌达到 target 或牌库空。"""
        if len(self.hand) >= target:
            return []
        need = target - len(self.hand)
        return self.draw_from_deck(need)

    def play_card(self, instance_id: str) -> Card | None:
        """从手牌打出一张牌（移除并返回）。"""
        for i, c in enumerate(self.hand):
            if c.instance_id == instance_id:
                return self.hand.pop(i)
        return None

    def find_card(self, instance_id: str) -> Card | None:
        """在手牌中查找。"""
        for c in self.hand:
            if c.instance_id == instance_id:
                return c
        return None

    def find_in_discard(self, instance_id: str, *, allow_frozen: bool = False) -> Card | None:
        """在弃牌堆中查找（默认排除冻结的）。"""
        for c in self.discard:
            if c.instance_id == instance_id and (allow_frozen or not c.frozen):
                return c
        return None

    def hand_to_dict(self) -> list[dict]:
        return [c.to_dict() for c in self.hand]

    def public_to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "is_ai": self.is_ai,
            "online": self.online,
            "ready": self.ready,
            "deck_count": len(self.deck),
            "hand_count": len(self.hand),
            "discard_count": len(self.discard),
            "reward_points": self.reward_points,
            "ended_round": self.ended_round,
        }

    # --------------------------------------------------------
    # 手牌上限（规则§一：上限 10）
    # --------------------------------------------------------
    def enforce_hand_limit(self) -> list[Card]:
        """行动结束时刻若手牌超 10，自动弃掉尾部的牌（简化策略）。

        规则要求"自选弃至 10 张"，这里简化为保留前 10 张，
        真实实现应由玩家通过奖励分"丢弃"操作选择。

        Returns:
            被弃掉的牌列表
        """
        cfg = get_game_config()
        limit = cfg.hand_limit
        if len(self.hand) <= limit:
            return []
        overflow = self.hand[limit:]
        self.hand = self.hand[:limit]
        for c in overflow:
            c.frozen = True
            self.discard.append(c)
        return overflow

    # --------------------------------------------------------
    # 回合内标记操作
    # --------------------------------------------------------
    def start_new_round(self) -> None:
        """回合开始时重置本回合内标记。

        注意：不在此处清零奖励分（规则§四.3：回合结束时清零），
        也不清空弃牌堆（规则§四.3：回合结束时返回牌池）。
        """
        self.ended_round = False
        self.ended_round_active = False
        self.last_action_order = 0
        self.last_active_end_order = 0
        self.last_passive_end_order = 0

    def end_round_active(self, order: int) -> None:
        """主动结束本回合。"""
        self.ended_round = True
        self.ended_round_active = True
        self.consecutive_active_ends += 1
        self.last_active_end_order = order

    def end_round_passive(self, order: int) -> None:
        """被动结束本回合。"""
        self.ended_round = True
        self.ended_round_active = False
        self.consecutive_active_ends = 0  # 被动重置连续计数
        self.last_passive_end_order = order

    def can_end_active(self) -> bool:
        """是否可主动结束回合（不能连续两回合主动）。"""
        return self.consecutive_active_ends < 1

    def record_action(self, order: int) -> None:
        """记录一次成功行动。"""
        self.last_action_order = order
        self.consecutive_active_ends = 0


__all__ = ["PlayerState"]
