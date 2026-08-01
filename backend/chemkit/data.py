"""chemkit.data：数据表加载与 Hess 运行时派生。

数据库最小化原则：JSON 只存规范可查的标准常数（E0/pKa/pKsp/logβ/ΔHf°），
派生量（反应焓 dH、半反应配平系数等）一律在加载时按 Hess 定律计算，不落地。
默认数据目录为包内 ./data（含 tests.json 测试用例库）。
"""
from __future__ import annotations
import json
from math import gcd
import os
from dataclasses import dataclass, field

from .core import elements_of, charge_of

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


# 单质标准态（ΔHf° ≡ 0，无需入库）
_ELEMENTS = {"Ag", "Al", "Au", "Ba", "Be", "C", "Ca", "Cd", "Co", "Cr", "Cs",
             "Cu", "Fe", "Ga", "Hg", "K", "Li", "Mg", "Mn", "Na", "Ni", "Pb",
             "Pt", "Rb", "S", "Sc", "Se", "Sn", "Sr", "Ti", "V", "Zn"}


@dataclass
class Tables:
    couples: list[dict]
    pka: list[dict]
    ksp: list[dict]
    beta: list[dict]
    conc: dict[str, float]
    ex: dict[str, dict]
    overrides: list[dict]
    thermo: dict[str, float] = field(default_factory=dict)   # 物种 ΔHf° (kJ/mol)
    # 派生
    cations: set[str] = field(default_factory=set)
    anions: set[str] = field(default_factory=set)
    ksp_by_pair: dict[tuple[str, str], dict] = field(default_factory=dict)
    ksp_by_solid: dict[str, dict] = field(default_factory=dict)
    beta_by_pair: dict[tuple[str, str], dict] = field(default_factory=dict)
    beta_by_complex: dict[str, dict] = field(default_factory=dict)
    pka_acid: dict[str, list[dict]] = field(default_factory=dict)   # acid -> entries
    pka_base: dict[str, list[dict]] = field(default_factory=dict)   # base -> entries
    solids: set[str] = field(default_factory=set)
    gases: set[str] = field(default_factory=set)
    # Henry 定律常数 H（mol/(L·kPa)，298K）：p = c/H。来源：NIST WebBook /
    # Sander, Atmos. Chem. Phys. 15, 4399 (2015) 汇编值（25℃，换算自 M/atm）
    henry: dict[str, float] = field(default_factory=lambda: {
        "CO_2": 3.4e-4,   # 0.034 M/atm（纯物理溶解，不含水合/电离）
        "O_2": 1.3e-5,    # 0.0013
        "H_2": 7.8e-6,    # 0.00078
        "N_2": 6.5e-6,    # 0.00065
        "NH_3": 0.57,     # ~57 M/atm（极易溶）
        "H_2S": 1.0e-3,   # 0.10
        "SO_2": 1.2e-2,   # 1.2
        "Cl_2": 6.2e-4,   # 0.062（物理溶解部分）
        "NO": 1.9e-5,     # 0.0019
        "N_2O": 2.5e-4,   # 0.025
        "CH_4": 1.4e-5,   # 0.0014
        "C_2H_2": 4.1e-4, # 0.041
        "PH_3": 8.0e-5,   # 0.008
        "H_2Se": 8.4e-4,  # 0.084
    })
    redox_species: set[str] = field(default_factory=set)   # 所有电对的 ox/red 物种
    redox_ox_E: dict[str, float] = field(default_factory=dict)    # 作为氧化剂的最高 E0
    redox_red_E: dict[str, float] = field(default_factory=dict)   # 作为还原剂的最低 E0


def _load(name: str):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


# 数据表缓存：按 data_dir 键缓存，确保同一数据目录在程序生命周期内只加载一次。
# import chemkit 时 system.TABLES = load_tables() 首次加载；后续任何
# load_tables() 调用（如 testsuit.main）直接返回缓存实例，零磁盘 I/O。
_TABLES_CACHE: dict[str, Tables] = {}


def load_tables(data_dir: str | None = None) -> Tables:
    global DATA_DIR
    if data_dir:
        DATA_DIR = data_dir
    cache_key = DATA_DIR
    cached = _TABLES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    ex = _load("substance_ex.json")
    # 浓溶液阈值（mol/L）作为物质属性并入 substance_ex.json 的 "conc_M" 字段，
    # 此处派生（原独立 conc.json 已废除——4 个阈值单建文件不值，且与
    # conc_forms 分居两处易改漏）
    conc = {name: e["conc_M"] for name, e in ex.items() if "conc_M" in e}
    t = Tables(
        couples=_load("couples.json"),
        pka=_load("pka.json"),
        ksp=_load("ksp.json"),
        beta=_load("beta.json"),
        conc=conc,
        ex=ex,
        overrides=_load("overrides.json"),
        thermo=_load("thermo.json"),
    )
    for e in t.ksp:
        cat, an = e["pair"]
        t.ksp_by_pair[(cat, an)] = e
        t.ksp_by_solid[e["solid"]] = e
        t.cations.add(cat)
        t.anions.add(an)
        t.solids.add(e["solid"])
    for e in t.beta:
        t.beta_by_pair[(e["center"], e["ligand"])] = e
        t.beta_by_complex[e["complex"]] = e
        (t.cations if charge_of(e["center"]) > 0 else t.anions).add(e["center"])
        (t.cations if charge_of(e["ligand"]) > 0 else t.anions).add(e["ligand"])
        (t.cations if charge_of(e["complex"]) > 0 else t.anions).add(e["complex"])
    for e in t.pka:
        t.pka_acid.setdefault(e["acid"], []).append(e)
        t.pka_base.setdefault(e["base"], []).append(e)
        for sp in (e["acid"], e["base"]):
            q = charge_of(sp)
            (t.cations if q > 0 else t.anions if q < 0 else set()).add(sp)
    for e in t.couples:
        for sp in (e["ox"], e["red"]):
            t.redox_species.add(sp)
            q = charge_of(sp)
            (t.cations if q > 0 else t.anions if q < 0 else set()).add(sp)
        # 物种作为氧化剂/还原剂参与的电势范围（惰性实现增益判定的近似配对用）
        t.redox_ox_E[e["ox"]] = max(t.redox_ox_E.get(e["ox"], -1e9), e["E0"])
        t.redox_red_E[e["red"]] = min(t.redox_red_E.get(e["red"], 1e9), e["E0"])
    # 补充：在 KSP 中无格子但参与拆解的离子
    t.anions.update(["NO_3^-", "ClO_4^-", "MnO_4^-", "ClO^-", "SO_3^{2-}", "HCO_3^-",
                     "HSO_3^-", "HS^-", "NO_2^-", "CN^-", "F^-", "Br^-", "I^-", "OH^-",
                     "BrO^-",
                     "CO_3^{2-}", "S^{2-}", "PO_4^{3-}", "HPO_4^{2-}", "H_2PO_4^-",
                     "SiO_3^{2-}", "H_2PO_2^-", "MnO_4^{2-}", "C_2^{2-}", "N^{3-}"])
    t.cations.update(["Na^+", "K^+", "NH_4^+", "H^+", "Li^+", "Sr^{2+}", "Ni^{2+}",
                      "Mn^{2+}", "Cr^{3+}", "Sn^{2+}"])
    for name, e in t.ex.items():
        if e.get("form") == "solid":
            t.solids.add(name)
        if e.get("form") == "gas":
            t.gases.add(name)
    _compute_dH(t)
    _TABLES_CACHE[cache_key] = t
    return t


# ---------- van't Hoff 反应焓的运行时派生（Hess 定律，dH 单位 kJ/mol） ----------

def _half_balance(ox: str, red: str):
    """通用还原半反应配平：k·ox + h·H+ + ne·e- -> m·red + w·H2O。
    骨架元素依次尝试非 H/O 元素、O、H（O2/H2O 等全 H/O 体系靠后两者）。
    返回 (k, h, m, w, ne) 或 None（元素单边出现的非标准形，走配体释放路径）。"""
    eo, er = elements_of(ox), elements_of(red)
    others = [X for X in set(eo) | set(er) if X not in ("H", "O")]
    for sk in others + ["O", "H"]:
        co0, cr0 = eo.get(sk, 0), er.get(sk, 0)
        if co0 == 0 or cr0 == 0:
            continue
        for k in (1, 2, 3, 4):
            if (k * co0) % cr0:
                continue
            m = (k * co0) // cr0
            if any(k * eo.get(X, 0) != m * er.get(X, 0) for X in others):
                break          # 骨架选择不当（元素单边出现），换下一骨架
            w = k * eo.get("O", 0) - m * er.get("O", 0)
            h = m * er.get("H", 0) + 2 * w - k * eo.get("H", 0)
            ne = k * charge_of(ox) + h - m * charge_of(red)
            if h < 0 or w < 0 or ne <= 0:
                break
            return k, h, m, w, ne
    return None


def _couple_dH(t, e):
    """电对还原半反应 ΔH 与电子数：标准半反应 -> 配体释放 -> SHE 约定。"""
    ox, red = e["ox"], e["red"]
    if (ox, red) == ("H^+", "H_2"):
        return 0.0, 2          # SHE：ΔG/ΔH/ΔS 全温度按定义为零
    bal = _half_balance(ox, red)
    if bal is not None:
        k, _h, m, w, ne = bal
        vals = [_dhf(t, red), _dhf(t, ox)]
        if all(v is not None for v in vals):
            return m * vals[0] + w * t.thermo["H_2O"] - k * vals[1], ne
        return None, None
    # 配体释放形：complex + ne·e- -> metal + nu·ligand（[Ag(NH3)2]+/Ag、
    # [AuCl4]-/Au 等）。配体数取自配合物化学式而非 beta 表的 nu（后者是
    # logβ 级数，可能与式中配体数不一致——[PtCl6]2- 的 beta nu=4 而式含
    # 6 Cl）；ne 由电荷平衡：ne = q(ox) − q(red) − nu·q(ligand)
    b = t.beta_by_complex.get(ox)
    if (b is not None and red in _ELEMENTS
            and elements_of(b["center"]).get(red)):
        lel = next((X for X in elements_of(b["ligand"]) if X != "H"), None)
        nu = (elements_of(ox).get(lel, 0) - elements_of(b["center"]).get(lel, 0)
              if lel else 0)
        vals = [_dhf(t, red), _dhf(t, b["ligand"]), _dhf(t, ox)]
        if nu > 0 and all(v is not None for v in vals):
            ne = charge_of(ox) - charge_of(red) - nu * charge_of(b["ligand"])
            if ne > 0:
                return vals[0] + nu * vals[1] - vals[2], ne
    return None, None


def _dhf(t, sp):
    if sp in t.thermo:
        return t.thermo[sp]
    return 0.0 if sp in _ELEMENTS else None


def _compute_dH(t):
    """四表反应焓运行时派生（不写回数据库）：
    couples 还原半反应（dH/dH_n）；pka 酸式电离 acid + w·H2O -> base + n·H+
    （水合物种不可省略：CO2 + H2O -> HCO3- + H+ 与 H2CO3 差一个 H2O）；
    ksp 溶解；beta 配位。缺 ΔHf 的条目不设 dH，引擎回退旧行为。"""
    for e in t.couples:
        v, ne = _couple_dH(t, e)
        if v is not None:
            e["dH"], e["dH_n"] = round(v, 1), ne
    for e in t.pka:
        a, b = _dhf(t, e["acid"]), _dhf(t, e["base"])
        if a is None or b is None:
            continue
        n = e.get("n", 1)
        w = (elements_of(e["base"]).get("H", 0) + n
             - elements_of(e["acid"]).get("H", 0)) / 2
        if w == int(w):
            e["dH"] = round(b - a - int(w) * t.thermo["H_2O"], 1)
    for e in t.ksp:
        cat, an = e["pair"]
        vals = [_dhf(t, e["solid"]), _dhf(t, cat), _dhf(t, an)]
        if all(v is not None for v in vals):
            qc, qa = charge_of(cat), -charge_of(an)
            g = gcd(qc, qa)
            e["dH"] = round(qa // g * vals[1] + qc // g * vals[2] - vals[0], 1)
    for e in t.beta:
        vals = [_dhf(t, e["complex"]), _dhf(t, e["center"]), _dhf(t, e["ligand"])]
        if all(v is not None for v in vals):
            e["dH"] = round(vals[0] - vals[1] - e["nu"] * vals[2], 1)
