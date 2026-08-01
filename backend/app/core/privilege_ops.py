"""特权卡操作处理器。

处理不直接参与反应的特权操作：
- extract（萃取）：从个人弃牌堆选 1 张物质牌加入手牌
- distill（蒸馏）：查看牌库顶 3 张，选 1 张加入手牌，其余按任意顺序放回牌库底

万能催化剂(wildcard)与强化物质牌(enhance_substance)在 action_processor 中处理
（因为与反应结算强耦合）。
强化条件牌(enhance_condition)也在 action_processor 中处理（修改条件牌 meta）。
"""

from __future__ import annotations

from app.connection.events import EVT_STATE_SYNC
from app.core.game_state import GameRoom, TurnPhase
from app.models.cards import Card, CardType, PrivilegeEffect
from app.services.logger import audit_event, get_logger

logger = get_logger(__name__)


def process_extract(
    room: GameRoom,
    player_id: str,
    target_card_id: str | None,
    privilege_card_id: str,
) -> None:
    """萃取：对个人弃牌堆使用。

    规则§五特权卡：
    - 选择 1 张物质牌加入手牌
    - 不可选取已冻结卡牌（本回合覆盖的旧物质）
    """
    player = room.get_player(player_id)
    if player is None:
        raise ValueError("玩家不存在")
    if room.current_player != player:
        room._enqueue_error(player_id, "not_your_turn", "非你的轮次")
        return

    if not target_card_id:
        room._enqueue_error(player_id, "missing_target", "需指定要萃取的卡")
        return

    # 校验特权卡在手牌
    priv = player.find_card(privilege_card_id)
    if priv is None or priv.type != CardType.PRIVILEGE:
        room._enqueue_error(player_id, "not_privilege_card", "特权卡不在手牌中")
        return

    # 在弃牌堆找卡（必须未冻结）
    target = player.find_in_discard(target_card_id, allow_frozen=False)
    if target is None:
        room._enqueue_error(
            player_id, "card_not_in_discard_or_frozen",
            "未在弃牌堆找到该卡，或卡已被冻结",
        )
        return
    if target.type != CardType.SUBSTANCE:
        room._enqueue_error(player_id, "extract_substance_only", "萃取仅能取物质牌")
        return

    # 执行：从弃牌堆移除，加入手牌
    player.discard.remove(target)
    target.frozen = False
    player.hand.append(target)

    # 消耗特权卡 → 游戏牌池
    player.play_card(privilege_card_id)
    room.card_pool.append(priv)

    audit_event(
        room.room_id, "privilege.extract",
        player_id=player_id, card=target.name,
    )
    room._enqueue_to_player(player_id, "privilege:extracted", {
        "card": target.to_dict(),
    })
    room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())


def process_distill(
    room: GameRoom,
    player_id: str,
    privilege_card_id: str,
    chosen_index: int | None = None,
) -> None:
    """蒸馏：对个人牌库使用。

    规则§五特权卡：
    - 查看牌库顶 3 张
    - 选 1 张加入手牌
    - 其余按任意顺序放回牌库底

    两阶段实现：
    1. 客户端调用 distill（不带 chosen_index）→ 服务端返回牌库顶 3 张
    2. 客户端选择后再次调用 distill（带 chosen_index）→ 服务端执行
    """
    player = room.get_player(player_id)
    if player is None:
        raise ValueError("玩家不存在")
    if room.current_player != player:
        room._enqueue_error(player_id, "not_your_turn", "非你的轮次")
        return

    # 校验特权卡
    priv = player.find_card(privilege_card_id)
    if priv is None or priv.type != CardType.PRIVILEGE:
        room._enqueue_error(player_id, "not_privilege_card", "特权卡不在手牌中")
        return

    # 阶段 1：返回牌库顶 3 张
    if chosen_index is None:
        top3 = player.deck[:3]
        room._enqueue_to_player(player_id, "privilege:distill_preview", {
            "cards": [c.to_dict() for c in top3],
            "indices": list(range(len(top3))),
        })
        return

    # 阶段 2：执行选择
    if not 0 <= chosen_index < min(3, len(player.deck)):
        room._enqueue_error(player_id, "invalid_index", "选择索引越界")
        return

    chosen = player.deck.pop(chosen_index)
    player.hand.append(chosen)

    # 消耗特权卡
    player.play_card(privilege_card_id)
    room.card_pool.append(priv)

    audit_event(
        room.room_id, "privilege.distill",
        player_id=player_id, card=chosen.name,
    )
    room._enqueue_to_player(player_id, "privilege:distilled", {
        "card": chosen.to_dict(),
    })
    room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())


__all__ = ["process_extract", "process_distill"]
