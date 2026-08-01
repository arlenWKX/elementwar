"""chemkit 输入输出数据模型。

将 chemkit 的 dict 结果包装为强类型 dataclass，
便于上层游戏逻辑层使用，同时降低对 chemkit 内部结构的耦合。

关键设计：
- 非 raw 字段（consumed/produced/final/equation）：过滤掉单纯电离/溶解后
  仅保留"真实化学反应"的净结果
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Degree = Literal["complete", "incomplete", "hardly", "none"]


# 视为"单纯电离/溶解"的 step.kind 集合
_NON_REACTION_KINDS = {"dissolve", "ionize"}

# 视为"环境物质"的物种（不应单独算作反应物/产物）
_ENVIRONMENT_SPECIES = {"H_2O", "O^{2-}"}


@dataclass(slots=True, frozen=True)
class Reactant:
    """反应物。"""

    name: str
    mol: float

    def to_dict(self) -> dict[str, float | str]:
        return {"name": self.name, "mol": self.mol}


@dataclass(slots=True, frozen=True)
class Conditions:
    """反应条件。

    与 chemkit conditions dict 直接对应。
    """

    V_L: float = 1.0
    T_K: float = 298.15
    p_kpa: float = 101.3
    c_H: float | None = None
    c_OH: float | None = None
    pH: float | None = None

    def to_dict(self) -> dict[str, float]:
        result: dict[str, float] = {"V_L": self.V_L, "T_K": self.T_K, "p_kpa": self.p_kpa}
        if self.c_H is not None:
            result["c_H"] = self.c_H
        if self.c_OH is not None:
            result["c_OH"] = self.c_OH
        if self.pH is not None:
            result["pH"] = self.pH
        return result


@dataclass(slots=True)
class ReactionResultData:
    """chemkit 反应结果（解包后的强类型）。

    字段均为过滤后的"真实化学反应"结果：
    - consumed/produced: 净消耗/净产物（排除单纯电离/溶解）
    - final: 反应后体系所有物种
    - steps: 仅保留真实反应步骤
    """

    reacted: bool
    degree: Degree
    consumed: dict[str, float]
    produced: dict[str, float]
    final: dict[str, float]
    ph: float | None
    equation: str | None
    annotations: list[str]
    override: str | None
    unknown: list[str]
    steps: list[dict]
    duration_ms: float = 0.0
    cached: bool = False

    @classmethod
    def from_chemkit(cls, raw: dict, duration_ms: float = 0.0, cached: bool = False) -> "ReactionResultData":
        """从 chemkit.judge 返回的 dict 构造。

        关键逻辑：
        1. 解析 raw 字段
        2. 过滤 steps：剔除 kind in _NON_REACTION_KINDS 的步骤（dissolve/ionize）
        3. 判定"水解离伪反应"
        4. 最终 reacted 判定
        """
        def _to_dict(items: list[dict] | dict) -> dict[str, float]:
            if isinstance(items, dict):
                return dict(items)
            return {it["name"]: float(it["mol"]) for it in items}

        consumed_raw = _to_dict(raw.get("consumed", []))
        produced_raw = _to_dict(raw.get("produced", []))
        final_raw = _to_dict(raw.get("final", []))
        all_steps = list(raw.get("steps", []) or [])

        real_steps = [
            s for s in all_steps
            if s.get("kind") not in _NON_REACTION_KINDS
        ]

        def _is_pseudo_water_step(step: dict) -> bool:
            if step.get("kind") != "proton":
                return False
            eq = step.get("equation", "")
            return "O^{2-}" in eq

        non_env_produced = {
            k: v for k, v in produced_raw.items()
            if k not in _ENVIRONMENT_SPECIES
        }
        non_env_consumed = {
            k: v for k, v in consumed_raw.items()
            if k not in _ENVIRONMENT_SPECIES
        }
        has_real_species_change = bool(non_env_produced) or bool(non_env_consumed)

        has_neutralize_step = any(
            s.get("kind") == "neutralize" for s in real_steps
        )

        _PSEUDO_PRODUCTION_SPECIES = {"H_2O", "O^{2-}", "OH^-", "H^+"}
        consumed_only_water_intermediate = all(
            k in _ENVIRONMENT_SPECIES for k in consumed_raw.keys()
        ) and bool(consumed_raw)
        produced_only_water_related = all(
            k in _PSEUDO_PRODUCTION_SPECIES for k in produced_raw.keys()
        )
        is_water_dissolution_pseudo = (
            consumed_only_water_intermediate
            and produced_only_water_related
            and not has_neutralize_step
        )

        if not has_real_species_change:
            real_steps = [s for s in real_steps if not _is_pseudo_water_step(s)]

        original_reacted = bool(raw.get("reacted", False))
        has_real_step = len(real_steps) > 0

        if original_reacted and is_water_dissolution_pseudo and not has_neutralize_step:
            reacted = False
            degree: Degree = "none"
        elif original_reacted and not has_real_step and not has_real_species_change and not has_neutralize_step:
            reacted = False
            degree: Degree = "none"
        else:
            reacted = original_reacted
            degree = raw.get("degree", "none")

        if reacted:
            consumed = non_env_consumed
            produced = non_env_produced
            if has_neutralize_step and not consumed and not produced:
                neutralize_step = next(s for s in real_steps if s.get("kind") == "neutralize")
                extent = float(neutralize_step.get("extent", 1.0) or 1.0)
                consumed = {"H^+": extent, "OH^-": extent}
        else:
            consumed = {}
            produced = {}

        final = final_raw

        equation = None
        if real_steps:
            for s in real_steps:
                eq = s.get("equation")
                if eq:
                    equation = eq
                    break

        return cls(
            reacted=reacted,
            degree=degree,
            consumed=consumed,
            produced=produced,
            final=final,
            ph=raw.get("final_pH") or raw.get("pH"),
            equation=equation,
            annotations=list(raw.get("annotations", []) or []),
            override=raw.get("override"),
            unknown=list(raw.get("unknown", []) or []),
            steps=real_steps,
            duration_ms=duration_ms,
            cached=cached,
        )

    def to_dict(self) -> dict:
        """序列化为 JSON 友好的 dict（前端推送用）。"""
        return {
            "reacted": self.reacted,
            "degree": self.degree,
            "consumed": self.consumed,
            "produced": self.produced,
            "final": self.final,
            "pH": self.ph,
            "equation": self.equation,
            "annotations": self.annotations,
            "override": self.override,
            "unknown": self.unknown,
            "steps": self.steps,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
        }


__all__ = ["Reactant", "Conditions", "ReactionResultData", "Degree"]
