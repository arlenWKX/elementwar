"""卡牌领域模型。

将规则文件中的牌类型抽象为强类型枚举与数据类，
供游戏逻辑层和连接层共享。

牌类型：
- 物质牌 (SUBSTANCE)：化学式 + 物质量 + 状态
- 条件牌 (CONDITION)：当前只有"加热"
- 特权卡 (PRIVILEGE)：万能催化剂/强化/萃取/蒸馏

配置文件化：
- 物质定义、条件牌定义、特权卡定义均从 app/data/materials.json 加载
- 全局游戏参数从 app/data/game_config.json 加载
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.services.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 枚举定义
# ============================================================
class CardType(str, Enum):
    """卡牌类型。"""

    SUBSTANCE = "substance"   # 物质牌
    CONDITION = "condition"  # 条件牌
    PRIVILEGE = "privilege"  # 特权卡


class PrivilegeEffect(str, Enum):
    """特权卡使用方式。

    一张牌面、多种用法，与规则文件一致。
    """

    WILDCARD = "wildcard"                    # 万能催化剂
    ENHANCE_SUBSTANCE = "enhance_substance"  # 强化物质牌
    ENHANCE_CONDITION = "enhance_condition"  # 强化条件牌（提升层级）
    EXTRACT = "extract"                       # 萃取
    DISTILL = "distill"                       # 蒸馏


class ConditionScope(str, Enum):
    """条件牌生效范围（被特权卡提升层级）。"""

    REACTION = "reaction"   # 当次反应（默认）
    ACTION = "action"       # 整个行动
    ROUND = "round"          # 整个轮次


class SubstanceForm(str, Enum):
    """物质状态。对应 chemkit substance_ex.form。"""

    MOLECULE = "molecule"
    SOLID = "solid"
    GAS = "gas"
    IONS = "ions"


# ============================================================
# 数据类
# ============================================================
@dataclass(slots=True, frozen=True)
class SubstanceDef:
    """物质定义（配置加载后不可变）。

    字段对应 materials.json substances 数组的一项。
    """

    name: str                          # chemkit 物质名，如 "Na", "H_2O"
    display_name: str                  # 显示名，如 "钠", "水"
    form: SubstanceForm = SubstanceForm.MOLECULE
    default_mol: float = 1.0           # 默认物质的量
    enhanced_mol: float | None = None  # 强化后物质的量，None 表示与 default 相同
    copies: int = 1                    # 牌池中张数
    category: str = ""                 # 类别（用于前端分组）
    note: str = ""                     # 备注

    @property
    def effective_enhanced_mol(self) -> float:
        """强化后的 mol（无定义则与默认相同）。"""
        return self.enhanced_mol if self.enhanced_mol is not None else self.default_mol


@dataclass(slots=True, frozen=True)
class ConditionDef:
    """条件牌定义。"""

    type: str                # 条件类型，目前只有 "heating"
    display_name: str
    target_temp_k: float | None = None   # 目标温度（仅加热）
    copies_per_player: int = 1           # 每玩家张数


@dataclass(slots=True)
class Card:
    """卡牌实例（每张牌有唯一 ID）。"""

    type: CardType
    # 物质牌：物质名；条件牌：条件类型；特权卡：固定 "privilege"
    name: str
    # 显示名
    display_name: str
    # 唯一 ID（用于手牌区分）
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # 附加属性
    meta: dict[str, Any] = field(default_factory=dict)
    # 是否冻结（弃牌堆中本回合不可用）
    frozen: bool = False

    def to_dict(self) -> dict[str, Any]:
        """序列化为前端可消费的字典。"""
        return {
            "instance_id": self.instance_id,
            "type": self.type.value,
            "name": self.name,
            "display_name": self.display_name,
            "meta": self.meta,
            "frozen": self.frozen,
        }


# ============================================================
# 物质注册表（从配置文件加载）
# ============================================================
class SubstanceRegistry:
    """物质定义注册表。

    启动时从 materials.json 加载，全局只读。
    """

    def __init__(self) -> None:
        self._substances: dict[str, SubstanceDef] = {}
        self._conditions: list[ConditionDef] = []
        self._privilege_effects: list[dict] = []
        self._loaded = False

    def load(self, materials_path: str | Path) -> None:
        """从 materials.json 加载全部定义。"""
        path = Path(materials_path)
        if not path.exists():
            logger.warning("materials.not_found", extra={"path": str(path)})
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for sub in data.get("substances", []):
            try:
                form = SubstanceForm(sub.get("form", "molecule"))
            except ValueError:
                form = SubstanceForm.MOLECULE
            self._substances[sub["name"]] = SubstanceDef(
                name=sub["name"],
                display_name=sub.get("display_name", sub["name"]),
                form=form,
                default_mol=float(sub.get("default_mol", 1.0)),
                enhanced_mol=float(sub["enhanced_mol"]) if sub.get("enhanced_mol") is not None else None,
                copies=int(sub.get("copies", 1)),
                category=sub.get("category", ""),
                note=sub.get("note", ""),
            )

        for cond in data.get("conditions", []):
            self._conditions.append(ConditionDef(
                type=cond["type"],
                display_name=cond.get("display_name", cond["type"]),
                target_temp_k=float(cond["target_temp_k"]) if cond.get("target_temp_k") else None,
                copies_per_player=int(cond.get("copies_per_player", 1)),
            ))

        self._privilege_effects = data.get("privileges", {}).get("effects", [])

        self._loaded = True
        logger.info(
            "materials.loaded",
            extra={
                "substances": len(self._substances),
                "conditions": len(self._conditions),
                "privileges": len(self._privilege_effects),
            },
        )

    @property
    def loaded(self) -> bool:
        return self._loaded

    def get(self, name: str) -> SubstanceDef | None:
        """按名查找物质定义。"""
        return self._substances.get(name)

    def all_substances(self) -> list[SubstanceDef]:
        """全部物质定义。"""
        return list(self._substances.values())

    @property
    def conditions(self) -> list[ConditionDef]:
        """全部条件牌定义。"""
        return list(self._conditions)

    @property
    def privilege_effects(self) -> list[dict]:
        """全部特权卡效果定义。"""
        return list(self._privilege_effects)


# 全局单例
_registry: SubstanceRegistry | None = None


def get_substance_registry() -> SubstanceRegistry:
    """获取物质注册表单例（懒加载）。"""
    global _registry
    if _registry is None:
        _registry = SubstanceRegistry()
        # 默认从 app/data/materials.json 加载
        from app.config import settings
        _registry.load(settings.materials_path)
    return _registry


# ============================================================
# 卡牌工厂
# ============================================================
def make_substance_card(name: str, *, enhanced: bool = False, mol: float | None = None) -> Card:
    """构造一张物质牌实例。

    Args:
        name: 物质名（如 "HCl"）
        enhanced: 是否为强化版本（True 用 enhanced_mol）
        mol: 显式指定 mol（覆盖默认/强化值）
    """
    registry = get_substance_registry()
    sub = registry.get(name)
    if sub is None:
        # 未知物质也允许构造（chemkit 会判断），但 display_name 用原名
        return Card(
            type=CardType.SUBSTANCE,
            name=name,
            display_name=name,
            meta={"mol": mol or 1.0, "form": "molecule", "enhanced": enhanced},
        )
    actual_mol = mol if mol is not None else (sub.effective_enhanced_mol if enhanced else sub.default_mol)
    return Card(
        type=CardType.SUBSTANCE,
        name=sub.name,
        display_name=sub.display_name,
        meta={
            "mol": actual_mol,
            "form": sub.form.value,
            "enhanced": enhanced,
            "category": sub.category,
            "default_mol": sub.default_mol,
            "enhanced_mol": sub.effective_enhanced_mol,
        },
    )


def make_condition_card(cond_type: str = "heating") -> Card | None:
    """构造一张条件牌实例。

    Args:
        cond_type: 条件类型，默认 "heating"
    """
    registry = get_substance_registry()
    for cond in registry.conditions:
        if cond.type == cond_type:
            return Card(
                type=CardType.CONDITION,
                name=cond.type,
                display_name=cond.display_name,
                meta={
                    "condition_type": cond.type,
                    "target_temp_k": cond.target_temp_k,
                },
            )
    return None


def make_privilege_card() -> Card:
    """构造一张特权卡实例。"""
    return Card(
        type=CardType.PRIVILEGE,
        name="privilege",
        display_name="特权卡",
        meta={},
    )


# ============================================================
# 牌池构建器
# ============================================================
def build_card_pool(num_players: int = 2) -> list[Card]:
    """根据玩家人数构建完整游戏牌池。

    牌数公式：
    - 物质牌：copies = 1 + (sub.copies - 1) × (num_players - 1)
      保留稀有物质的稀缺性，同时随人数线性放大基础物质张数
    - 条件牌：copies_per_player × num_players
    - 特权卡：总数 = num_players
    """
    pool: list[Card] = []
    registry = get_substance_registry()

    # 物质牌：按 copies 公式动态计算
    for sub in registry.all_substances():
        copies = 1 + (sub.copies - 1) * (num_players - 1)
        copies = max(1, copies)
        for _ in range(copies):
            pool.append(make_substance_card(sub.name))

    # 条件牌：copies_per_player × 人数
    for cond in registry.conditions:
        copies = cond.copies_per_player * num_players
        for _ in range(copies):
            card = make_condition_card(cond.type)
            if card is not None:
                pool.append(card)

    # 特权卡：总数 = 人数
    for _ in range(num_players):
        pool.append(make_privilege_card())

    return pool


__all__ = [
    "CardType",
    "PrivilegeEffect",
    "ConditionScope",
    "SubstanceForm",
    "SubstanceDef",
    "ConditionDef",
    "Card",
    "SubstanceRegistry",
    "get_substance_registry",
    "make_substance_card",
    "make_condition_card",
    "make_privilege_card",
    "build_card_pool",
]
