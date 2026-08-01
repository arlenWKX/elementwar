"""游戏玩法接口（无需认证，供客户端查询游戏元信息与反应预览）。

RESTful 设计：
- POST /api/game/reactions:preview  — 预览反应（不进游戏流程）
- GET  /api/game/substances         — 列出所有物质定义
- GET  /api/game/rules              — 游戏规则与反应条件常量
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.chemkit_adapter.adapter import dry_run, list_products
from app.chemkit_adapter.models import Conditions, Reactant
from app.config import get_game_config
from app.models.cards import get_substance_registry
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/api/game", tags=["game"])


# ============================================================
# 请求/响应模型
# ============================================================
class ReactantInput(BaseModel):
    """反应物输入。"""
    name: str = Field(description="物质名，如 'HCl', 'NaOH'")
    mol: float = Field(default=1.0, gt=0, description="物质的量 mol")


class ReactionPreviewRequest(BaseModel):
    """反应预览请求。"""
    reactants: list[ReactantInput] = Field(min_length=2, max_length=4)
    heated: bool = Field(default=False, description="是否加热")


class ReactionPreviewResponse(BaseModel):
    """反应预览结果。"""
    reacted: bool = Field(description="是否发生反应")
    products: list[str] = Field(default_factory=list, description="新产物名称列表（已排除反应物）")
    equation: str | None = Field(default=None, description="配平方程式")
    degree: str = Field(default="none", description="反应程度: complete/incomplete/hardly/none")
    annotations: list[str] = Field(default_factory=list, description="注释")


# ============================================================
# 接口
# ============================================================
@router.post("/reactions:preview", response_model=ApiResponse)
async def preview_reaction(req: ReactionPreviewRequest) -> ApiResponse:
    """预览反应结果（不进入游戏流程，不写缓存）。

    用于：
    - 客户端高亮"可打"的牌
    - AI Bot 决策预判
    - 调试 chemkit

    返回的 products 已排除与反应物同名的物种。

    设计说明：
    - 使用 `:preview` 动作后缀（Google API 风格），表示对 reactions 资源执行预览操作
    - 不写缓存，避免污染游戏内反应缓存
    """
    cfg = get_game_config()
    reactants = [Reactant(name=r.name, mol=r.mol) for r in req.reactants]
    cond = Conditions(
        V_L=cfg.default_volume_l,
        T_K=cfg.heating_temperature_k if req.heated else cfg.default_temperature_k,
        p_kpa=cfg.default_pressure_kpa,
    )
    result = await dry_run(reactants, cond)
    products = await list_products(reactants, cond)
    return ApiResponse(data=ReactionPreviewResponse(
        reacted=result.reacted,
        products=products,
        equation=result.equation,
        degree=result.degree,
        annotations=result.annotations,
    ).model_dump())


@router.get("/substances", response_model=ApiResponse)
async def list_substances() -> ApiResponse:
    """列出所有物质定义（供客户端显示卡牌信息）。"""
    registry = get_substance_registry()
    substances = []
    for sub in registry.all_substances():
        substances.append({
            "name": sub.name,
            "display_name": sub.display_name,
            "form": sub.form.value,
            "category": sub.category,
            "default_mol": sub.default_mol,
            "enhanced_mol": sub.effective_enhanced_mol,
            "copies": sub.copies,
            "note": sub.note,
        })
    conditions = []
    for cond in registry.conditions:
        conditions.append({
            "type": cond.type,
            "display_name": cond.display_name,
            "target_temp_k": cond.target_temp_k,
            "copies_per_player": cond.copies_per_player,
        })
    return ApiResponse(data={
        "substances": substances,
        "conditions": conditions,
        "privilege_effects": registry.privilege_effects,
    })


@router.get("/rules", response_model=ApiResponse)
async def get_rules() -> ApiResponse:
    """返回游戏规则与反应条件常量。"""
    cfg = get_game_config()
    return ApiResponse(data={
        "reaction": {
            "default_volume_l": cfg.default_volume_l,
            "default_temperature_k": cfg.default_temperature_k,
            "heating_temperature_k": cfg.heating_temperature_k,
            "default_pressure_kpa": cfg.default_pressure_kpa,
        },
        "room": {
            "min_players": cfg.room_min_players,
            "max_players": cfg.room_max_players,
            "hand_size_init": cfg.hand_size_init,
            "deck_size_init": cfg.deck_size_init,
            "hand_limit": cfg.hand_limit,
        },
        "reward": {
            "chain_reward_start_step": cfg.chain_reward_start_step,
            "chain_reward_per_step": cfg.chain_reward_per_step,
            "exchange_costs": cfg.exchange_costs,
        },
        "victory": {
            "require_empty_deck": cfg.require_empty_deck_to_win,
            "require_empty_hand": cfg.require_empty_hand_to_win,
        },
    })


__all__ = ["router"]
