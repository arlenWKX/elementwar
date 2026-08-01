"""行动处理器：实现"接龙"反应流程。

跨层数据流：
1. 连接层转发 action:react 事件
2. 本模块校验玩家手牌、场上物质、条件牌/特权卡
3. 调用 chemkit_adapter 计算反应
4. 应用结果到 GameRoom（更新场上物质、移动卡牌、奖励分）
5. 返回待广播事件（由连接层发送）

反应体系默认：水溶液 1L, 298.15K, 101.3 kPa。
条件牌只有"加热"：将温度提升到 game_config.heating_temperature_k（默认 353.15K = 80°C）。

卡牌流转（规则§二）：
- 物质牌：手牌→场上→弃牌堆(冻结)→回合结束返回牌池
- 条件牌：手牌→弃牌堆(冻结)→回合结束返回牌池
- 特权卡：手牌→牌池（直接）

公开 API：
- process_react_action(): 处理玩家接龙
- end_action_active(): 主动结束回合（不出牌）
- end_action(): 正常结束行动（连锁结束）
- end_action_passive(): 被动结束行动（无有效反应）
- apply_chosen_product(): 应用玩家选择的产物
- check_victory(): 胜利判定
- declare_winner(): 宣告胜利
"""

from __future__ import annotations

import time
from typing import Any

from app.connection.events import (
    EVT_ACTION_CHOOSE_PRODUCT,
    EVT_ACTION_ENDED,
    EVT_ACTION_REACT_FAILED,
    EVT_ACTION_REACT_OK,
    EVT_CARDS_DECK_ADDED,
    EVT_GAME_ENDED,
    EVT_REWARD_EARNED,
    EVT_STATE_SYNC,
    EVT_TURN_NEXT_PLAYER
)
from app.chemkit_adapter.adapter import react
from app.chemkit_adapter.models import Conditions, Reactant, ReactionResultData
from app.config import get_game_config
from app.core.game_state import GameRoom, RoomPhase, TurnPhase
from app.models.cards import (
    Card,
    CardType,
    PrivilegeEffect,
    make_substance_card,
)
from app.models.schemas import ReactActionPayload
from app.services.logger import audit_event, get_logger

logger = get_logger(__name__)


# ============================================================
# 条件牌 → chemkit Conditions 映射
# ============================================================
def conditions_from_cards(
    base: Conditions | None,
    condition_cards: list[Card],
    privilege_effect: str | None = None,
) -> Conditions:
    """将条件牌转换为 chemkit Conditions。

    当前条件牌只有 "heating"，将温度提升到 game_config.heating_temperature_k。
    其他条件（浓稀、压力等）从物质牌本身的 enhanced_mol 控制（通过特权卡强化触发）。
    """
    cfg = get_game_config()
    cond = base or Conditions(
        V_L=cfg.default_volume_l,
        T_K=cfg.default_temperature_k,
        p_kpa=cfg.default_pressure_kpa,
    )

    if not condition_cards:
        return cond

    for card in condition_cards:
        ctype = card.meta.get("condition_type", card.name)
        if ctype == "heating":
            target_t = card.meta.get("target_temp_k") or cfg.heating_temperature_k
            if target_t > cond.T_K:
                cond = Conditions(
                    V_L=cond.V_L,
                    T_K=float(target_t),
                    p_kpa=cond.p_kpa,
                    c_H=cond.c_H,
                    c_OH=cond.c_OH,
                    pH=cond.pH,
                )

    return cond


def default_conditions() -> Conditions:
    """返回默认反应条件（1L, 298.15K, 101.3kPa）。"""
    cfg = get_game_config()
    return Conditions(
        V_L=cfg.default_volume_l,
        T_K=cfg.default_temperature_k,
        p_kpa=cfg.default_pressure_kpa,
    )


# ============================================================
# 接龙处理
# ============================================================
async def process_react_action(
    room: GameRoom,
    player_id: str,
    payload: ReactActionPayload,
) -> None:
    """处理玩家的"接龙"行动。

    流程：
    1. 若是本玩家本回合首次行动 → 调用 start_action（行动低保+重置临时状态+milestone）
    2. 校验：玩家轮次、手牌存在性、特权卡合法性
    3. 抽出物质牌 + 条件牌 + 特权卡
    4. 构造 Reactant 列表（物质牌 + 场上物质）
    5. 构造 Conditions
    6. 调用 chemkit_adapter.react()
    7. 若成功：
       - 若 produced 有多个，进入 AWAITING_PRODUCT_CHOICE
       - 若 produced 仅一个，直接应用
       - 移动卡牌（按规则§二流转）、加奖励分（连锁第 3 步起每步 +1）
    8. 若失败：
       - 若是首次行动无连锁历史 → 被动结束
       - 否则行动结束
    """
    player = room.get_player(player_id)
    if player is None:
        raise ValueError("玩家不存在")
    if room.current_player != player:
        room._enqueue_error(player_id, "not_your_turn", "非你的轮次")
        return
    if room.turn_phase == TurnPhase.AWAITING_PRODUCT_CHOICE:
        room._enqueue_error(player_id, "pending_choice", "请先选择产物")
        return

    # 1. 若是本行动首次操作（无 chain_history）→ start_action
    # start_action 会触发：行动低保、重置临时状态、action_seq+1
    # milestone 检查在 _finalize_action 中统一进行（行动结束时）
    # 注意：不能用 chain_step==0 或 played_cards 判断，
    # 因为打出初始物质后 chain_step 仍为 0，played_cards 可能保留场上物质引用
    if not room.chain_history and room.chain_step == 0:
        try:
            room.start_action(player_id)
        except ValueError as e:
            room._enqueue_error(player_id, "cannot_start_action", str(e))
            return

    # 2. 校验并取出物质牌
    substance_card = player.find_card(payload.substance_card_id)
    if substance_card is None:
        room._enqueue_error(player_id, "card_not_in_hand", "物质牌不在手牌中")
        return
    if substance_card.type != CardType.SUBSTANCE:
        room._enqueue_error(player_id, "not_substance_card", "打出的牌不是物质牌")
        return

    # 3. 校验条件牌：每种类型限 1 张
    condition_cards: list[Card] = []
    seen_condition_types: set[str] = set()
    for cid in payload.condition_card_ids:
        c = player.find_card(cid)
        if c is None:
            room._enqueue_error(player_id, "card_not_in_hand", f"条件牌 {cid} 不在手牌中")
            return
        if c.type != CardType.CONDITION:
            room._enqueue_error(player_id, "not_condition_card", f"卡 {cid} 不是条件牌")
            return
        ct = c.meta.get("condition_type", c.name)
        if ct in seen_condition_types:
            room._enqueue_error(
                player_id, "duplicate_condition_type",
                f"每种条件牌只能打出 1 张：{ct}",
            )
            return
        seen_condition_types.add(ct)
        condition_cards.append(c)

    # 4. 校验特权卡（每次行动限 1 张）
    privilege_card: Card | None = None
    privilege_effect: str | None = None
    if payload.privilege_card_id:
        privilege_card = player.find_card(payload.privilege_card_id)
        if privilege_card is None:
            room._enqueue_error(player_id, "card_not_in_hand", "特权卡不在手牌中")
            return
        if privilege_card.type != CardType.PRIVILEGE:
            room._enqueue_error(player_id, "not_privilege_card", "打出的牌不是特权卡")
            return
        if not payload.privilege_effect:
            room._enqueue_error(player_id, "missing_effect", "特权卡需指定使用方式")
            return
        privilege_effect = payload.privilege_effect

    # 5. 万能催化剂：标记，反应失败时强制成功
    is_wildcard = privilege_effect == PrivilegeEffect.WILDCARD.value

    # 6. 处理强化特权卡：物质牌 mol 提升到 enhanced_mol
    if privilege_effect == PrivilegeEffect.ENHANCE_SUBSTANCE.value:
        enhanced_mol = substance_card.meta.get("enhanced_mol", substance_card.meta.get("mol", 1.0))
        player.hand.remove(substance_card)
        substance_card = make_substance_card(substance_card.name, enhanced=True)
        player.hand.append(substance_card)
        room._enqueue_to_player(player_id, "card:enhanced", {
            "instance_id": substance_card.instance_id,
            "name": substance_card.name,
            "mol": enhanced_mol,
        })

    # 6.5 处理强化条件牌：提升条件牌生效范围（reaction → action → round）
    # 标记到条件牌 meta 中，供 conditions_from_cards 参考
    if privilege_effect == PrivilegeEffect.ENHANCE_CONDITION.value and condition_cards:
        for cc in condition_cards:
            current_scope = cc.meta.get("scope", "reaction")
            if current_scope == "reaction":
                cc.meta["scope"] = "action"
            elif current_scope == "action":
                cc.meta["scope"] = "round"
            # round 是最高级，不再提升
        room._enqueue_to_player(player_id, "card:condition_enhanced", {
            "condition_card_ids": [c.instance_id for c in condition_cards],
            "scopes": [c.meta.get("scope") for c in condition_cards],
        })

    # 7. 构造反应输入
    substance_name = substance_card.name
    substance_mol = float(substance_card.meta.get("mol", 1.0))

    reactants: list[Reactant] = []
    if room.field_substance is None:
        # 回合内首次行动：物质牌作为初始场上物质（不需要反应）
        # 按规则§五接龙："若玩家为回合内首次行动，玩家从手牌中选择一张作为初始场上物质"
        # 此时物质牌从手牌移出但不进弃牌堆（它是场上物质，不是被覆盖的旧物质）
        played = player.play_card(substance_card.instance_id)
        if played is None:
            room._enqueue_error(player_id, "card_not_in_hand", "物质牌不在手牌中")
            return
        room.field_substance = substance_name
        room.field_substance_mol = substance_mol
        room.played_cards_this_action.append(played)
        # 注意：初始场上物质不进弃牌堆（它是场上物质），后续被覆盖时才进弃牌堆
        # 但需要追踪它，以便在 _apply_reaction_result 时把它进弃牌堆
        # 这里简化：played_cards_this_action 已记录，覆盖时处理
        room._enqueue_to_player(player_id, EVT_ACTION_REACT_OK, {
            "step": 0,
            "field_substance": substance_name,
            "initial": True,
        })
        room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())

        # 初始场上物质设置后，玩家可选择继续连锁或结束
        # 此处不强制结束，等玩家 emit end_action 或继续 react
        return

    reactants.append(Reactant(name=room.field_substance, mol=room.field_substance_mol))
    reactants.append(Reactant(name=substance_name, mol=substance_mol))

    # 标记已尝试反应（用于 end_action_active 判定主动/被动结束）
    room.attempted_reaction = True

    # 8. 构造条件
    base_cond = default_conditions()
    cond = conditions_from_cards(base_cond, condition_cards, privilege_effect)

    # 9. 调用 chemkit
    result: ReactionResultData = await react(reactants, cond, use_cache=True)
    room.pending_reaction_result = result.to_dict()

    if not result.reacted:
        # 万能催化剂：反应失败时强制成功
        # 规则§五：本次反应无需满足常规反应条件即可强制触发
        if is_wildcard:
            # 构造一个强制成功的结果：产物 = 物质牌本身（简化策略）
            # 真实实现可让玩家从物质牌 + 场上物质中选一个作为产物
            forced_product = substance_name
            result = ReactionResultData(
                reacted=True,
                degree="complete",
                consumed={r.name: r.mol for r in reactants},
                produced={forced_product: substance_mol},
                final={forced_product: substance_mol},
                ph=None,
                equation=f"{room.field_substance} + {substance_name} → {forced_product} (万能催化剂)",
                annotations=["wildcard_forced"],
                override="wildcard",
                unknown=[],
                steps=[{"kind": "wildcard", "equation": f"→ {forced_product}", "extent": 1.0}],
                duration_ms=result.duration_ms,
                cached=False,
            )
            room.pending_reaction_result = result.to_dict()
            audit_event(
                room.room_id, "action.wildcard_forced",
                player_id=player_id,
                reactants=[r.name for r in reactants],
                product=forced_product,
            )
            # 万能催化剂直接应用结果（跳过产物过滤逻辑，因为 forced_product 可能与反应物同名）
            room.chain_step += 1
            _apply_played_cards(
                room, player, substance_card, condition_cards, privilege_card,
                update_field=True, chosen_product=forced_product, result=result,
            )
            _award_chain_reward(room, player)
            room.chain_history.append({
                "step": room.chain_step,
                "reactants": [r.name for r in reactants],
                "product": forced_product,
                "equation": result.equation,
                "degree": result.degree,
                "annotations": result.annotations,
            })
            if check_victory(room, player):
                declare_winner(room, player_id, reason="deck_empty_and_hand_empty")
                return
            if not payload.continue_chain:
                end_action(room, player)
            else:
                room.turn_phase = TurnPhase.IDLE
                room._enqueue_to_player(player_id, EVT_ACTION_REACT_OK, {
                    "step": room.chain_step,
                    "field_substance": room.field_substance,
                    "equation": result.equation,
                    "degree": result.degree,
                    "products": [forced_product],
                    "continue_chain": True,
                    "wildcard": True,
                })
                room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())
            return
        else:
            # 反应失败：消耗特权卡（打出即消耗），但不消耗物质牌/条件牌
            # 规则§五：特权卡使用后返回游戏牌池
            if privilege_card is not None:
                played_priv = player.play_card(privilege_card.instance_id)
                if played_priv is not None:
                    room.card_pool.append(played_priv)
                    room.played_cards_this_action.append(played_priv)

            audit_event(
                room.room_id, "action.react_failed",
                player_id=player_id,
                reactants=[r.name for r in reactants],
                annotations=result.annotations,
            )
            room._enqueue_to_player(player_id, EVT_ACTION_REACT_FAILED, {
                "reactants": [r.name for r in reactants],
                "annotations": result.annotations,
                "unknown": result.unknown,
            })
            # 不自动结束行动：让玩家换牌重试。
            # 玩家可主动 end_turn 结束（若无成功反应则被动结束，无连续惩罚）。
            # 连锁中失败也保留已连锁结果，玩家可继续尝试或 end_action 停止。
            return

    # 10. 反应成功
    room.chain_step += 1

    # 产物候选：优先用 produced（净产物），若为空（如中和反应 spectator ions），
    # 则用 final（反应后体系所有物种）减去反应物
    reactant_names = {r.name for r in reactants}
    products = list(result.produced.keys())
    if not products:
        # 中和反应等场景：用 final 中的非环境物种作为候选
        env_species = {"H_2O", "O^{2-}", "H^+", "OH^-"}
        products = [
            name for name in result.final.keys()
            if name not in env_species and name not in reactant_names
        ]
    new_products = [p for p in products if p not in reactant_names]

    if len(new_products) == 0:
        room._enqueue_error(player_id, "no_new_product", "反应未产生新物质，可换牌重试")
        return

    chosen_product: str
    if len(new_products) == 1:
        chosen_product = new_products[0]
    elif payload.chosen_product and payload.chosen_product in new_products:
        chosen_product = payload.chosen_product
    else:
        # 需要玩家选择 — 先应用卡牌流转（移除手牌、旧物质进弃牌堆等），
        # 但不更新 field_substance（等玩家选产物后再更新）
        _apply_played_cards(
            room, player, substance_card, condition_cards, privilege_card,
            update_field=False, chosen_product=None, result=result,
        )
        room.pending_products = new_products
        room.turn_phase = TurnPhase.AWAITING_PRODUCT_CHOICE
        room._enqueue_to_player(player_id, EVT_ACTION_CHOOSE_PRODUCT, {
            "products": new_products,
            "reactants": [r.name for r in reactants],
            "result": result.to_dict(),
        })
        return

    # 11. 应用反应结果（卡牌流转 + 更新场上物质）
    _apply_played_cards(
        room, player, substance_card, condition_cards, privilege_card,
        update_field=True, chosen_product=chosen_product, result=result,
    )

    # 12. 奖励分：从第 N 步起每步 +K
    _award_chain_reward(room, player)

    # 13. 连锁历史
    room.chain_history.append({
        "step": room.chain_step,
        "reactants": [r.name for r in reactants],
        "product": chosen_product,
        "equation": result.equation,
        "degree": result.degree,
        "annotations": result.annotations,
    })

    # 14. 胜利判定（规则§七：打出最后一张手牌并结算完该次反应瞬间）
    if check_victory(room, player):
        declare_winner(room, player_id, reason="deck_empty_and_hand_empty")
        return

    # 15. 继续连锁或结束
    if not payload.continue_chain:
        end_action(room, player)
    else:
        room.turn_phase = TurnPhase.IDLE
        room._enqueue_to_player(player_id, EVT_ACTION_REACT_OK, {
            "step": room.chain_step,
            "field_substance": room.field_substance,
            "equation": result.equation,
            "degree": result.degree,
            "products": new_products,
            "continue_chain": True,
        })
        room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())


# ============================================================
# 卡牌流转（统一实现，消除 _pre_apply_for_choice / _apply_reaction_result 重复）
# ============================================================
def _apply_played_cards(
    room: GameRoom,
    player,
    substance_card: Card,
    condition_cards: list[Card],
    privilege_card: Card | None,
    *,
    update_field: bool,
    chosen_product: str | None,
    result: ReactionResultData,
) -> None:
    """应用本次反应的卡牌流转。

    按规则§二：
    - 旧场上物质 → 玩家弃牌堆（冻结至回合结束）
    - 物质牌（玩家本次打出的）→ 游戏牌池
    - 条件牌 → 玩家弃牌堆（冻结）
    - 特权卡 → 游戏牌池（处理 phantom 销毁）

    Args:
        update_field: True 时更新 field_substance 为 chosen_product（反应完成）；
                      False 时仅流转卡牌，不更新场上物质（等待玩家选产物）
        chosen_product: update_field=True 时必填
        result: 反应结果（用于取产物 mol）
    """
    # 1. 旧场上物质 → 玩家弃牌堆（冻结）
    # 旧场上物质可能是本行动的初始物质（在 played_cards_this_action 中）
    # 或上一步反应的产物（无 Card 实例，需构造一张）
    if room.field_substance is not None:
        initial_card = next(
            (c for c in room.played_cards_this_action
             if c.type == CardType.SUBSTANCE and c.name == room.field_substance
             and not c.frozen),
            None,
        )
        if initial_card is not None:
            room.played_cards_this_action.remove(initial_card)
            initial_card.frozen = True
            player.discard.append(initial_card)
        else:
            old_card = make_substance_card(room.field_substance, mol=room.field_substance_mol)
            old_card.frozen = True
            player.discard.append(old_card)

    # 2. 物质牌从手牌移出 → 游戏牌池
    played = player.play_card(substance_card.instance_id)
    if played is not None:
        room.played_cards_this_action.append(played)
        room.card_pool.append(played)

    # 3. 条件牌 → 弃牌堆（冻结）
    for c in condition_cards:
        played_cond = player.play_card(c.instance_id)
        if played_cond is not None:
            played_cond.frozen = True
            player.discard.append(played_cond)
            room.played_cards_this_action.append(played_cond)

    # 4. 特权卡 → 游戏牌池（处理 phantom 销毁）
    if privilege_card is not None:
        played_priv = player.play_card(privilege_card.instance_id)
        if played_priv is not None:
            phantom_in_pool = next(
                (c for c in room.card_pool
                 if c.type == CardType.PRIVILEGE and c.meta.get("phantom")),
                None,
            )
            if phantom_in_pool is not None:
                # 规则§六：销毁 phantom，新特权卡不进牌池
                room.card_pool.remove(phantom_in_pool)
                audit_event(
                    room.room_id, "privilege.phantom_destroyed",
                    player_id=player.player_id,
                )
            else:
                room.card_pool.append(played_priv)
            room.played_cards_this_action.append(played_priv)

    # 5. 更新场上物质（仅在反应完成时）
    if update_field and chosen_product:
        # 产物 mol：优先从 produced 取，回退到 final，再回退到 1.0
        # （中和反应等场景 produced 为空，需从 final 取）
        product_mol = result.produced.get(chosen_product)
        if product_mol is None:
            product_mol = result.final.get(chosen_product, 1.0)
        room.field_substance = chosen_product
        room.field_substance_mol = float(product_mol)


def _award_chain_reward(room: GameRoom, player) -> None:
    """连锁奖励分：从第 N 步起每步 +K（默认 N=3, K=1）。"""
    cfg = get_game_config()
    if room.chain_step >= cfg.chain_reward_start_step:
        player.reward_points += cfg.chain_reward_per_step
        room._enqueue_to_player(player.player_id, EVT_REWARD_EARNED, {
            "step": room.chain_step,
            "points": cfg.chain_reward_per_step,
            "total": player.reward_points,
        })


# ============================================================
# 玩家选择产物（多产物场景）
# ============================================================
def apply_chosen_product(room: GameRoom, player_id: str, chosen_product: str) -> None:
    """玩家在 AWAITING_PRODUCT_CHOICE 阶段选择产物后调用。

    此时卡牌流转已在 process_react_action 中通过 _apply_played_cards(update_field=False) 完成，
    这里更新 field_substance、补发连锁奖励、追加连锁历史、检查胜利。

    注意：chain_step 已在 process_react_action 第 10 步 +1，此处不再重复递增。
    """
    player = room.get_player(player_id)
    if player is None:
        return
    if room.turn_phase != TurnPhase.AWAITING_PRODUCT_CHOICE:
        room._enqueue_error(player_id, "not_awaiting_choice", "当前不在选产物阶段")
        return
    if chosen_product not in room.pending_products:
        room._enqueue_error(player_id, "invalid_product", "产物不在候选列表中")
        return

    # 从 pending_reaction_result 取产物 mol
    product_mol = 1.0
    result_dict = room.pending_reaction_result or {}
    final = result_dict.get("final", {})
    produced = result_dict.get("produced", {})
    if chosen_product in produced:
        product_mol = float(produced[chosen_product])
    elif chosen_product in final:
        product_mol = float(final[chosen_product])

    room.field_substance = chosen_product
    room.field_substance_mol = product_mol
    room.turn_phase = TurnPhase.IDLE
    room.pending_products.clear()

    # 补发连锁奖励（与单产物路径一致）
    _award_chain_reward(room, player)

    # 追加连锁历史
    room.chain_history.append({
        "step": room.chain_step,
        "reactants": list(result_dict.get("consumed", {}).keys()),
        "product": chosen_product,
        "equation": result_dict.get("equation", ""),
        "degree": result_dict.get("degree", ""),
        "annotations": result_dict.get("annotations", []),
    })

    room._enqueue_to_player(player_id, EVT_ACTION_REACT_OK, {
        "step": room.chain_step,
        "field_substance": chosen_product,
        "chosen": True,
        "equation": result_dict.get("equation", ""),
        "continue_chain": True,
    })
    room._enqueue_broadcast(EVT_STATE_SYNC, room._build_state_snapshot())

    # 胜利判定
    if check_victory(room, player):
        declare_winner(room, player_id, reason="deck_empty_and_hand_empty")
        return


# ============================================================
# 主动/被动结束行动
# ============================================================
def end_action_active(room: GameRoom, player_id: str) -> None:
    """主动结束回合（不出牌或不再继续）。

    - 若本次行动有成功反应（chain_history 非空）→ 主动结束（有连续惩罚）
    - 若本次行动尝试过反应但全部失败 → 被动结束（无连续惩罚）
    - 若未尝试反应直接结束 → 主动结束（有连续惩罚）

    action_seq 在 start_action 中已 +1（代表一次行动），此处不再 +1。
    """
    player = room.get_player(player_id)
    if player is None:
        raise ValueError("玩家不存在")
    if room.current_player != player:
        room._enqueue_error(player_id, "not_your_turn", "非你的轮次")
        return

    # 若本玩家本回合首次操作（无 chain_history 且 chain_step==0），需先 start_action
    if not room.chain_history and room.chain_step == 0:
        try:
            room.start_action(player_id)
        except ValueError as e:
            room._enqueue_error(player_id, "cannot_start_action", str(e))
            return

    # 判定主动 vs 被动：
    # 有成功反应 → 主动；尝试过但无成功反应 → 被动；未尝试 → 主动
    has_success = bool(room.chain_history)
    # attempted_reaction 标记在 process_react_action 中设置（玩家尝试过反应）
    attempted = getattr(room, "attempted_reaction", False)

    if has_success:
        # 主动结束（有连续惩罚）
        if not player.can_end_active():
            room._enqueue_error(
                player_id, "consecutive_active_end",
                "不能连续两回合主动结束",
            )
            return
        player.end_round_active(room.action_seq)
        kind = "active"
        audit_event(room.room_id, "action.ended_active", player_id=player_id, action_seq=room.action_seq)
    elif attempted:
        # 尝试过反应但全部失败 → 被动结束（无连续惩罚）
        player.end_round_passive(room.action_seq)
        kind = "passive"
        audit_event(room.room_id, "action.ended_passive", player_id=player_id, action_seq=room.action_seq)
    else:
        # 未尝试反应直接结束 → 主动结束（有连续惩罚）
        if not player.can_end_active():
            room._enqueue_error(
                player_id, "consecutive_active_end",
                "不能连续两回合主动结束",
            )
            return
        player.end_round_active(room.action_seq)
        kind = "active"
        audit_event(room.room_id, "action.ended_active", player_id=player_id, action_seq=room.action_seq)

    room._enqueue_broadcast(EVT_ACTION_ENDED, {
        "player_id": player_id,
        "kind": kind,
        "action_seq": room.action_seq,
    })

    _finalize_action(room, player)


def end_action(room: GameRoom, player) -> None:
    """正常结束行动（连锁结束）。公开 API。

    action_seq 在 start_action 中已 +1，此处不再 +1。
    """
    player.record_action(room.action_seq)
    room.turn_phase = TurnPhase.IDLE

    audit_event(room.room_id, "action.ended", player_id=player.player_id, kind="chain_end")
    room._enqueue_broadcast(EVT_ACTION_ENDED, {
        "player_id": player.player_id,
        "kind": "chain_end",
        "action_seq": room.action_seq,
    })
    _finalize_action(room, player)


def end_action_passive(room: GameRoom, player) -> None:
    """被动结束行动（整次行动无有效反应）。公开 API。

    action_seq 在 start_action 中已 +1，此处不再 +1。
    """
    player.end_round_passive(room.action_seq)
    audit_event(room.room_id, "action.ended_passive", player_id=player.player_id)
    room._enqueue_broadcast(EVT_ACTION_ENDED, {
        "player_id": player.player_id,
        "kind": "passive",
        "action_seq": room.action_seq,
    })
    _finalize_action(room, player)


def _finalize_action(room: GameRoom, player) -> None:
    """行动结束的统一收尾：手牌上限、清理追踪列表、推进轮次、检查里程碑。

    注意：场上物质（含初始物质）不在行动结束时回收。
    - 若被接龙覆盖，旧物质已在 _apply_played_cards 中进弃牌堆
    - 若未被覆盖（如打出初始物质后直接结束），留在场上给下一玩家接龙
    - 仅在回合结束 end_turn 时，场上物质才返回牌池
    """
    # 规则§一：行动结束时刻若手牌超 10，自选弃至 10
    overflow = player.enforce_hand_limit()
    if overflow:
        room._enqueue_to_player(player.player_id, "cards:overflow_discarded", {
            "cards": [c.to_dict() for c in overflow],
            "reason": "hand_limit_exceeded",
        })

    # 清理本行动的追踪列表
    # 注意：已流转到 card_pool/discard 的牌不需要从 played_cards_this_action 移除，
    # 直接清空即可（played_cards_this_action 只是本行动的临时追踪列表）
    # 但若场上物质仍是初始物质（未流转），需保留其引用以便 end_turn 时回收
    field_substance_name = room.field_substance
    initial_card_on_field = None
    if field_substance_name is not None:
        initial_card_on_field = next(
            (c for c in room.played_cards_this_action
             if c.type == CardType.SUBSTANCE and c.name == field_substance_name
             and not c.frozen),
            None,
        )

    room.played_cards_this_action.clear()
    # 重置行动内状态（chain_step / chain_history），为下一玩家的新行动做准备
    room.chain_step = 0
    room.chain_history.clear()
    room.attempted_reaction = False

    # 若场上物质仍是初始物质，保留其引用（重新加入 played_cards_this_action）
    # 以便 end_turn 时回收
    if initial_card_on_field is not None:
        room.played_cards_this_action.append(initial_card_on_field)

    # 行动里程碑检查（在 action_seq 已 +1 后）
    _check_milestone(room)

    # 推进到下一玩家或结束本回合
    _advance_after_action(room)


def _advance_after_action(room: GameRoom) -> None:
    """行动结束后推进。"""
    has_next = room.advance_to_next_player()
    if not has_next:
        room.end_turn()
    else:
        next_player = room.current_player
        if next_player:
            room._enqueue_broadcast(EVT_TURN_NEXT_PLAYER, {
                "player_id": next_player.player_id,
                "round_no": room.round_no,
            })


def _check_milestone(room: GameRoom) -> None:
    """行动里程碑：本回合累计总行动次数每经过 人数×3，每人抽 1 张加入牌库。

    规则§三。
    """
    cfg = get_game_config()
    n_players = len(room.players)
    if n_players == 0:
        return
    threshold = n_players * cfg.milestone_actions_per_player
    if room.action_seq > 0 and room.action_seq % threshold == 0:
        for player in room.players:
            if room.card_pool:
                drawn = room.card_pool.pop()
                player.deck.append(drawn)
                room._enqueue_to_player(
                    player.player_id, EVT_CARDS_DECK_ADDED,
                    {"card_type": drawn.type.value, "source": "milestone"},
                )


# ============================================================
# 胜利判定（规则§七）
# ============================================================
def check_victory(room: GameRoom, player) -> bool:
    """胜利条件（规则§七）：

    当玩家同时满足以下两项时，在其打出最后一张手牌并结算完该次反应瞬间，立即获胜：
    1. 个人牌库中没有任何牌
    2. 手牌中仅剩该张被打出的牌，且打出后手牌为 0

    本函数在 _apply_reaction_result 之后调用，此时手牌已 pop，所以检查：
    - deck 空
    - hand 空
    """
    cfg = get_game_config()
    if cfg.require_empty_deck_to_win and len(player.deck) > 0:
        return False
    if cfg.require_empty_hand_to_win and len(player.hand) > 0:
        return False
    return True


def declare_winner(room: GameRoom, player_id: str, reason: str = "standard") -> None:
    """宣告胜利并触发游戏结束。"""
    room.phase = RoomPhase.FINISHED
    room.winner_id = player_id
    audit_event(room.room_id, "game.ended", winner_id=player_id, reason=reason)
    room._enqueue_broadcast(EVT_GAME_ENDED, {
        "winner_id": player_id,
        "reason": reason,
    })


__all__ = [
    "process_react_action",
    "end_action_active",
    "end_action",
    "end_action_passive",
    "apply_chosen_product",
    "check_victory",
    "declare_winner",
    "conditions_from_cards",
    "default_conditions",
]
