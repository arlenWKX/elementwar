"""奖励分兑换处理器。

规则文件 §六：
- 1 ★：回收（已打出的牌加入手牌）
- 1 ★：获取（牌库顶抽 1 张）
- 1 ★：丢弃（手牌弃 1 张到弃牌堆）
- 2 ★：兑换特权卡（从游戏牌池取 1 张特权卡）

代价表来自 game_config.reward.exchange_costs，可热加载。
"""

from __future__ import annotations

from app.connection.events import (
    EVT_CARDS_DRAWN,
    EVT_REWARD_EXCHANGED,
    EVT_STATE_SYNC
)
from app.config import get_game_config
from app.core.game_state import GameRoom, TurnPhase
from app.models.cards import Card, CardType
from app.models.schemas import RewardExchangePayload
from app.services.logger import audit_event, get_logger

logger = get_logger(__name__)


def process_reward_exchange(
    room: GameRoom,
    player_id: str,
    payload: RewardExchangePayload,
) -> None:
    """处理奖励分兑换。"""
    player = room.get_player(player_id)
    if player is None:
        raise ValueError("玩家不存在")
    if room.current_player != player:
        room._enqueue_error(player_id, "not_your_turn", "非你的轮次")
        return

    cfg = get_game_config()
    cost = cfg.exchange_cost(payload.kind)
    if player.reward_points < cost:
        room._enqueue_error(player_id, "insufficient_reward", f"奖励分不足，需要 {cost}")
        return

    if payload.kind == "recycle":
        _do_recycle(room, player, payload.target_card_id)
    elif payload.kind == "draw":
        _do_draw(room, player)
    elif payload.kind == "discard":
        _do_discard(room, player, payload.target_card_id)
    elif payload.kind == "exchange_privilege":
        _do_exchange_privilege(room, player)

    player.reward_points -= cost
    audit_event(
        room.room_id, "reward.exchanged",
        player_id=player_id, kind=payload.kind,
        cost=cost, remaining=player.reward_points,
    )
    room._enqueue_to_player(player_id, EVT_REWARD_EXCHANGED, {
        "kind": payload.kind,
        "cost": cost,
        "remaining": player.reward_points,
    })
    room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())


def _do_recycle(room: GameRoom, player, target_card_id: str | None) -> None:
    """回收：反应结算时从已打出的牌中选 1 张加入手牌。

    按规则§六：
    - 不可选特权卡
    - 选中的牌不返回游戏牌池（物质牌）/弃牌堆（条件牌），改为加入手牌

    "已打出的牌"按卡牌类型分布：
    - 物质牌 → card_pool（按规则§二流转）
    - 条件牌、旧场上物质 → player.discard（冻结）
    - 特权卡 → card_pool（但不可回收）
    - 初始场上物质（未被覆盖前）→ played_cards_this_action（未流转）
    """
    if not target_card_id:
        room._enqueue_error(player.player_id, "missing_target", "需指定要回收的牌")
        return

    # 在 played_cards_this_action 中查找引用（这是本行动所有打出牌的统一追踪列表）
    target = next(
        (c for c in room.played_cards_this_action if c.instance_id == target_card_id),
        None,
    )
    if target is None:
        room._enqueue_error(player.player_id, "card_not_played", "未在已打出的牌中找到该卡")
        return
    if target.type == CardType.PRIVILEGE:
        room._enqueue_error(player.player_id, "no_recycle_privilege", "不可回收特权卡")
        return

    # 从实际位置移除（牌只会在一个位置，用 try/except 简化）
    _try_remove(room.card_pool, target)
    _try_remove(player.discard, target)
    _try_remove(room.played_cards_this_action, target)

    # 加入手牌（解冻）
    target.frozen = False
    player.hand.append(target)


def _try_remove(lst: list, item) -> None:
    """安全地从列表移除元素（不存在则忽略）。"""
    try:
        lst.remove(item)
    except ValueError:
        pass


def _do_draw(room: GameRoom, player) -> None:
    """获取：从牌库顶抽 1 张到手牌。"""
    drawn = player.draw_from_deck(1)
    if not drawn:
        room._enqueue_error(player.player_id, "deck_empty", "牌库已空")
        return
    room._enqueue_to_player(player.player_id, EVT_CARDS_DRAWN, {
        "cards": [c.to_dict() for c in drawn],
        "source": "reward_draw",
    })


def _do_discard(room: GameRoom, player, target_card_id: str | None) -> None:
    """丢弃：手牌弃 1 张到弃牌堆。

    通过丢弃，手牌至少保留 1 张。
    """
    if not target_card_id:
        room._enqueue_error(player.player_id, "missing_target", "需指定要丢弃的牌")
        return
    if len(player.hand) <= 1:
        room._enqueue_error(player.player_id, "must_keep_one", "通过丢弃，手牌至少保留 1 张")
        return
    card = player.play_card(target_card_id)
    if card is None:
        room._enqueue_error(player.player_id, "card_not_in_hand", "未在手牌中找到该卡")
        return
    card.frozen = True
    player.discard.append(card)


def _do_exchange_privilege(room: GameRoom, player) -> None:
    """兑换特权卡：从游戏牌池取 1 张特权卡到手牌。

    按规则§六：若游戏牌池中无特权卡，临时补充一张，
    后续第一张进入牌池的特权卡销毁（用 meta["phantom"] 标记追踪，
    在 action_processor._apply_reaction_result 中检查销毁）。
    """
    # 优先取非 phantom 的特权卡
    privilege = next(
        (c for c in room.card_pool
         if c.type == CardType.PRIVILEGE and not c.meta.get("phantom")),
        None,
    )
    if privilege is None:
        # 牌池无真实特权卡 → 临时补充一张 phantom
        from app.models.cards import make_privilege_card
        privilege = make_privilege_card()
        privilege.meta["phantom"] = True
        # phantom 直接加入手牌，不进牌池
    else:
        room.card_pool.remove(privilege)
    player.hand.append(privilege)


__all__ = ["process_reward_exchange"]
