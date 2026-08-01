"""chemkit.engine：水溶液反应判定主引擎（预测 walk 与 pH 估计）。

原 engine.py v2 内核（architecture-v2.md 的实现）。

六条核心：
  ① 候选即平衡（logK 来自电对/pKa/pKsp/logβ 或其 Hess 精确加和）；
  ② pH 连续，由账本现场推算；
  ③ S = logK − logQ（实际活度），永远生效；
  ④ 执行 = 二分求解平衡程度 x*（S(x*)=0 或计量上限）；
  ⑤ H_excess 单一带符号账本，方程一律 H+ 正则形（无 OH-）；
  ⑥ 程度判定按极限试剂转化率。

动力学层（与热力学显式分离）：gate 闸门 / slow 标注 / 膜 blocked /
溶剂优先（仅此一条界面动力学规则）/ OVERRIDE 逃生舱。
"""
from __future__ import annotations
import heapq
import os as _os
from dataclasses import dataclass, field
from math import gcd, log10, sqrt
from .core import (elements_of, charge_of, balance,
                     K_NERNST_298, PKW_298, k_nernst, _vant, pKw_of)
from .data import Tables

# ---------------------------------------------------------- 全局常数（§7 纪律：唯一、文档化）
# ---- 外界气相模型：总压 101.3 kPa 惰性环境（外界气相不含产物气体）----
# 热力学气体活度 a = p/p°（p°=101.325 kPa 标准压力）。
# 自产气体：惰性环境下外界 p_i≈0，气体不断逸出直至残余分压与扫气速率达到
# 稳态；按残余分压约定 P_RES=1 kPa（开放烧杯自然对流量级）→ a≈1e-2。
# 外加供给气体（投料中的气体试剂）：可溶气体试剂（氨水、氯水等）真实形态
# 是溶质，按溶质活度 a=c/c°（"持续供给"语义由账本浓度承载）——不用
# p/p°≈1，否则 NH3 络合溶解（AgCl+浓氨水）等全部被低估。
# 自产气体按泡点判据（见 S_of 气体分支）。
P_STD_KPA = 101.325             # 热力学标准压力
P_EXT_KPA = 101.3               # 外界气相总压（惰性环境）
P_RES_KPA = 1.0                 # 逸出气体残余分压约定（惰性扫气稳态）
A_GAS = P_RES_KPA / P_STD_KPA   # 逸出气体活度（产物侧）≈ 1e-2，全局唯一
ACT_FLOOR = 1e-12               # 溶质活度数值下限（只是种子，程度由零点决定）
X_MIN = 1e-6                    # 可忽略程度（mol）
SOLVENT_FIRST = 10.0            # 溶剂优先阈值（界面动力学规则，全引擎仅此一条）
BLOCKED_EXTENT = 0.02           # 膜封锁时的痕量程度
SAT_SKIP = 0.05                 # 固相形态规则：饱和活度低于此值则跳过裸离子路径
ANN_MIN_EXTENT = 1e-3           # 慢反应标注的显著程度阈值（mol，低于此量级不标注）
STALL_FRAC = 0.01               # 停滞步阈值（ext/x_max）：执行但禁用该方向且不解禁其他，
                                # 让次优候选接手（破局刚性耦合：Cu2+/少量氨水的沉淀-解配互锁）
DEGREE_COMPLETE = 0.99
DEGREE_PARTIAL = 0.05
MAX_ITER = 3000
# 水溶液中优先与水反应的活泼金属（教材规则）
WATER_FIRST_METALS = {"Li", "Na", "K", "Rb", "Cs", "Ca", "Sr", "Ba"}
# 非金属单质固相：S(IV)-金属规则的例外（SO2 还原含氧酸制非金属单质是工业反应，
# 如 H2SeO3+2SO2+H2O->Se+2H2SO4 用于硒提纯）
NONMETAL_SOLIDS = {"C", "S", "Se", "Si", "P_4", "I_2", "B"}
# 卤酸根集合与歧化解锁温度（教材规则：卤素+冷稀碱 -> XO-，+热碱 -> XO3-；
# 歧化经 XO- 中间体，其进一步歧化室温慢——漂白液室温稳定即此动力学事实）
# 卤素歧化到卤酸根的慢化温度（分卤素，文献动力学）：ClO- -> ClO3- 室温慢、
# 热碱快（教材"冷稀碱 -> ClO-，热碱 -> ClO3-"，漂白液室温稳定）→ T_lim=340。
# BrO-/IO- 歧化快，但直接歧化需经 XO- 中间体、由 OH- 催化：中性水中 X2 歧化
# 动力学封闭（氯水/溴水/碘水皆稳定），强碱中解锁——T_lim 取极大值表示"无温度
# 解锁"，由 HALATE_BASE_PH 给出碱解锁阈值：Br2+2OH- -> BrO- 平衡常数 ~1e11
# （Kh(Br2)=5.8e-9, Ka(HBrO)=2e-9），pH≥9 即显著；I2 体系 K≈4.6e4
# （Kh(I2)=2e-13, Ka(HIO)=2.3e-11），pH≥12 才显著（碘量法 pH 5-8 操作窗口、
# pH 11 碘仍稳定，即 D44 NaClO+KI 终态）。
HALATE_DISP_T = {"ClO_3^-": 340.0, "BrO_3^-": 1e9, "IO_3^-": 1e9}
HALATE_BASE_PH = {"BrO_3^-": 9.0, "IO_3^-": 13.0}
# 固相 -> 阳离子映射（ksp 表），供活泼金属规则识别固相氢氧化物氧化剂
_HALF_CACHE: dict = {}

def _half_scale(c: dict) -> float:
    # 半反应中每个 red 粒子对应的电子数 = n / (red 系数)。
    # red 系数由活性元素（非 O/H）原子守恒推出：IO3-/I2 -> 0.5，I2/I- -> 2
    key = (c["ox"], c["red"])
    if key in _HALF_CACHE:
        return _HALF_CACHE[key]
    eo = elements_of(c["ox"])
    er = elements_of(c["red"])
    nu = 1.0
    for el, cnt in eo.items():
        if el in ("O", "H"):
            continue
        if er.get(el):
            nu = cnt / er[el]
            break
    else:
        # 两侧均只含 O/H（如 H2O2/H2O、O2/OH-）：按 O 原子数折算
        if eo.get("O") and er.get("O"):
            nu = eo["O"] / er["O"]
    scale = c["n"] / nu if nu else float(c["n"])
    _HALF_CACHE[key] = scale
    return scale
_TRACE = bool(_os.environ.get("CHEM_TRACE"))
WATER = "H_2O"
H_ION = "H^+"
WATER_MOL_PER_L = 55.6
# 浓溶液中以分子态记账的强酸（HCl 任何浓度全电离，不在此列——原条目永不命中，已移除）
STRONG_MOLECULAR_ACIDS = {"H_2SO_4": 2, "HNO_3": 1}
# estimate_state 静态缓存中的强酸标记（pKa<=0，已在 normalize 拆解，此处仅作
# h_c 直读上限贡献）；用对象哨兵避免与 None（不在表中）歧义
_STRONG_ACID = object()








@dataclass(slots=True)
class Cand:
    kind: str                 # redox / proton / precip / dissolve / complex / decomplex / derived
    r: dict                   # 反应物（H+ 正则形，可含 "H^+" 与 "H_2O"）
    pr: dict                  # 产物
    logK: float               # 298 K 值；redox 为 n*dE0/0.05916
    pkw_coeff: float = 0.0    # logK(T) = logK + pkw_coeff*(pKw(T)-14)
    redox: tuple | None = None  # (n_e, dE0) —— redox 的 logK 随 T 变
    dH: float | None = None   # 反应焓 kJ/mol（正向书写式，**不含水电离部分**——
                              # 该部分恒由 pkw_coeff 通道承载；None=无数据回退）
    meta: dict = field(default_factory=dict)
    _key: tuple | None = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> tuple:
        """候选唯一键（(sorted_r, sorted_pr)）。r/pr 创建后不可变，缓存避免
        重复 sorted+tuple（原 206k 次调用每代重排，是 judge 主循环的隐性热点）。"""
        k = self._key
        if k is None:
            k = (tuple(sorted(self.r.items())), tuple(sorted(self.pr.items())))
            self._key = k
        return k


def logK_T(c: Cand, T_K: float) -> float:
    """logK(T) 三通道：redox Nernst 缩放（无 dH 时的回退）/ van't Hoff（有 dH）
    + pkw_coeff 水电离部分。van't Hoff 在 298.15 K 恒等，493 套件零扰动。"""
    if c.redox is not None:
        n, dE0 = c.redox
        if c.dH is not None:
            return n * dE0 / K_NERNST_298 + _vant(c.dH, T_K)
        return n * dE0 / k_nernst(T_K)
    vant = _vant(c.dH, T_K) if c.dH is not None else 0.0
    return c.logK + c.pkw_coeff * (pKw_of(T_K) - PKW_298) + vant


# ========================================================== 规范化

def _mol_fraction(name: str, c: float, T) -> float:
    """强酸分子分数（水活度驱动的协同缔合代理曲线）：f = c^p/(c_half^p + c^p)。
    单一表观 Ka 无法同时拟合稀区（≤4M 分子分数<1%）与浓区（≥10M 显著
    分子化）——浓区分子化本质是活度系数/水活度效应而非稀溶液 Ka。
    两个常数均有物理含义：c_half = 半分子化浓度（HNO3 12M、H2SO4 12M，
    与 Raman 形态数据量级一致），p = 协同度。替代原 conc_M 二元开关。
    无 spec 字段的酸（HCl 等——浓盐酸实际仍近全电离）恒为 0。"""
    spec = (T.ex.get(name) or {}).get("spec")
    if spec is None or c <= 0.0:
        return 0.0
    cp = c ** spec["p"]
    return cp / (spec["c_half"] ** spec["p"] + cp)


def normalize(substances: list[dict], cond: dict, T) -> tuple[dict, float, list, list[str]]:
    V = cond["V_L"]
    ledger: dict[str, float] = {}
    unknown: list[str] = []
    pos = 0.0   # 强酸 H+ 贡献
    neg = 0.0   # 强碱 OH- 贡献

    def add_species(name: str, mol: float):
        nonlocal pos, neg
        if name == H_ION:
            pos += mol
        elif name == "OH^-":
            neg += mol
        else:
            ledger[name] = ledger.get(name, 0.0) + mol

    for item in substances:
        name, mol = item["name"], float(item["mol"])
        if mol <= 0:
            continue
        entry = T.ex.get(name)
        if charge_of(name) != 0 and (name in T.cations or name in T.anions):
            add_species(name, mol)
        elif entry is not None:
            if "conc_forms" in entry:
                # 连续形态分布：分子分数由表观电离平衡给出（不再按 conc_M
                # 一刀切）；电离部分仍按全电离约定拆为离子
                f_mol = _mol_fraction(name, mol / V, T)
                m_mol = mol * f_mol
                if m_mol > 0.0:
                    ledger[name] = ledger.get(name, 0.0) + m_mol
                if mol - m_mol > 0.0:
                    for sp, nu in _split_acid(name, T).items():
                        add_species(sp, nu * (mol - m_mol))
                # 影子库存：该酸（仅本酸，不含盐类同离子）的分子+离子总量，
                # 供逐轮再平衡求解目标分子池（输出时过滤 "__" 前缀）
                ledger[f"__tot_{name}"] = ledger.get(f"__tot_{name}", 0.0) + mol
            elif entry["form"] == "ions":
                for ion in entry["ions"]:
                    add_species(ion, mol)
            else:  # molecule / solid / gas 原样入账
                ledger[name] = ledger.get(name, 0.0) + mol
        elif charge_of(name) == 0:
            parts = _split_acid(name, T) or _split_salt(name, T)
            if parts is None:
                unknown.append(name)
            elif parts == "solid":
                ledger[name] = ledger.get(name, 0.0) + mol
            else:
                for sp, nu in parts.items():
                    add_species(sp, nu * mol)
        else:
            unknown.append(name)

    # 用户条件给定的初始酸碱性
    if cond.get("c_H"):
        pos += cond["c_H"] * V
    if cond.get("c_OH"):
        neg += cond["c_OH"] * V
    if cond.get("pH") is not None:
        pos += 10.0 ** (-cond["pH"]) * V

    steps0 = []
    neutralized = min(pos, neg)
    if neutralized > 1e-9:
        steps0.append({"kind": "neutralize", "equation": "H+ + OH- -> H2O",
                       "logK": PKW_298, "S": PKW_298, "extent": round(neutralized, 6),
                       "conversion": 1.0})
    H_excess = pos - neg
    ledger[WATER] = ledger.get(WATER, 0.0) + WATER_MOL_PER_L * V
    return ledger, H_excess, steps0, unknown


def _split_acid(name: str, T) -> dict | None:
    elems = elements_of(name)
    if "H" not in elems:
        return None
    n_h = elems["H"]
    for an in T.anions:
        q = -charge_of(an)
        if q <= 0 or n_h % q != 0:
            continue
        k = n_h // q
        target = dict(elements_of(an))
        target["H"] = target.get("H", 0) + k * q
        if target == elems:
            return {H_ION: n_h, an: k}
    return None


def _split_salt(name: str, T):
    elems = elements_of(name)
    for cat in sorted(T.cations, key=lambda c: -charge_of(c)):
        qc = charge_of(cat)
        if qc <= 0:
            continue
        ec = elements_of(cat)
        if any(elems.get(k, 0) < v for k, v in ec.items()):
            continue
        for an in T.anions:
            qa = -charge_of(an)
            if qa <= 0:
                continue
            ea = elements_of(an)
            for m in range(1, 5):
                if (m * qc) % qa != 0:
                    continue
                n = (m * qc) // qa
                tot: dict = {}
                for k, v in ec.items():
                    tot[k] = tot.get(k, 0) + m * v
                for k, v in ea.items():
                    tot[k] = tot.get(k, 0) + n * v
                if tot == elems:
                    cell = T.ksp_by_pair.get((cat, an))
                    if cell is not None and not cell.get("slight"):
                        return "solid"
                    return {cat: m, an: n}
    return None


# ========================================================== pH 估计器（§3.2，教科书近似）

def _buffer_titration(ledger: dict, H_excess: float, V: float, T, pKw: float,
                      multilevel: bool = False, T_K: float = 298.15) -> tuple:
    """He>0：强酸被在账弱碱（Kb 大者先）吸收 B+H+→HB；He<0：强碱被在账弱酸
    （Ka 大者先）吸收 HA+OH-→A-+H2O。全吸收 → Henderson 定 pH（返回）；
    残余超过 1e-3 mol/L → None（交回直读分支）；无储备 → None。"""
    """返回 (pH|None, 残余He, 虚拟账本)。滴定在虚拟账本上真实记账（base→acid
    或 acid→base 转化），全吸收后分支 4 必须用虚拟账本评估残余酸碱性——
    否则强酸恰好中和全部弱碱时，原账本里的弱碱会虚报碱性（pH 11 假象）。
    pH 非 None：缓冲对 Henderson 定 pH；pH=None 且残余≈0：落分支4（用虚拟账本）。"""
    if abs(H_excess) < 1e-12:
        return None, H_excess, ledger
    # 静态预计算（每数据表一次）：碱储备列表、酸储备列表、beta_pka 配离子储备。
    # 原实现每次调用全表扫描并做 max/min/列表解析与 startswith 过滤，是最大热点
    if getattr(T, "_titr_static", None) is None:
        # pKa 静态量按"每质子"预存，van't Hoff 修正 dH/n 在调用时按 T_K 施加
        # （缓存与温度无关：dH 本身是常数）
        bases = []
        for base, entries in T.pka_base.items():
            if base in T.solids or base == WATER:
                continue
            e1s = [e for e in entries if e["n"] == 1] or entries
            e_max = max(e1s, key=lambda e: e["pka"] / e["n"])
            pka = e_max["pka"] / e_max["n"]
            dH_pp = e_max["dH"] / e_max["n"] if "dH" in e_max else None
            acid = e1s[0]["acid"] if e1s[0]["acid"] != WATER else None
            if acid is not None:
                bases.append((pka, dH_pp, base, acid))
        bases.sort(key=lambda x: -x[0])
        acids = []
        for acid, entries in T.pka_acid.items():
            if acid in T.solids or acid == WATER:
                continue
            e1 = min(entries, key=lambda e: e["pka"])
            if e1["pka"] <= 0:
                continue   # 强酸已由 He 直读处理
            dH_pp = e1["dH"] / e1["n"] if "dH" in e1 else None
            acids.append((e1["pka"], dH_pp, acid, e1["base"]))
        acids.sort(key=lambda x: x[0])
        beta_pka = []
        for dc in build_derived(T):
            if not dc.meta.get("src", "").startswith("beta_pka:"):
                continue
            nu_h = dc.r.get(H_ION, 0)
            if nu_h <= 0:
                continue
            comps = [s for s in dc.r if s not in (H_ION, WATER)]
            if len(comps) != 1:
                continue
            beta_pka.append((comps[0], dc, nu_h))
        T._titr_static = (bases, acids, beta_pka,
                          {b: (p, d, a) for p, d, b, a in bases},
                          {a: (p, d, b) for p, d, a, b in acids})
    _bases, _acids, _beta_pka, _bases_map, _acids_map = T._titr_static

    def _pka_eff(pka: float, dH_pp) -> float:
        # pKa = −logKa：van't Hoff 修正对 pKa 变号（吸热电离 T 升 pKa 降）
        return pka - (_vant(dH_pp, T_K) if dH_pp is not None else 0.0)
    # 有效 pKa/配离子储备强度只依赖 T_K——按温度缓存，避免每次调用对
    # 全部储备条目重算 _pka_eff/logK_T（_buffer_titration 是最大热点）
    eff_cache = getattr(T, "_titr_eff", None)
    if eff_cache is None:
        eff_cache = T._titr_eff = {}
    eff = eff_cache.get(T_K)
    if eff is None:
        eff = ({b: _pka_eff(p, d) for p, d, b, a in _bases},
               {a: _pka_eff(p, d) for p, d, a, b in _acids},
               {c: logK_T(dc, T_K) / nh for c, dc, nh in _beta_pka},
               {c: (dc, nh) for c, dc, nh in _beta_pka})
        eff_cache[T_K] = eff
    _beff, _aeff, _ceff, _cmap = eff
    # 延迟拷贝：仅当 heap 非空、确实需要修改账本时才 dict(ledger)。
    # heap 为空（强酸/强碱+盐等无弱组分场景）直接返回原账本——judge 中
    # `vled is not ledger` 身份检查据此跳过 _virt_redox_gain（无滴定=无虚拟增益）
    ledger2: dict | None = None
    he = H_excess
    # 多级（多元）滴定用堆：快照在账量入堆，转化产物若本身仍可继续
    # 质子化/去质子化（HVO3→VO2+、H2PO4-→HPO4^2-…）则按自身 pKa 重新入堆，
    # 一次调用沿质子化梯走到底——快照式单级实现会把中间形态（如 HVO3）
    # 滞留为假象，后续氧化还原竞争因此看不到真实自由形态（VO2+）
    if he > 0:   # 弱碱吸收：pKa(共轭酸) 越大 Kb 越大，先中和
        heap = []
        cnt = 0
        # 遍历在账物种（通常 ~15 个）而非全储备表（~50 条），热点降载；
        # 配离子作为碱储备（beta_pka 派生：complex + νH+ -> center + ν共轭酸）：
        # 沉淀等步骤释放的 H+ 实际由配离子解离吸收（如 [Cu(NH3)4]2+），
        # 不纳入会使 solve_extent 内部 pH 崩塌、反应假停滞（Cu2+ + 少量氨水）
        for sp, m in ledger.items():
            if m <= X_MIN:
                continue
            binfo = _bases_map.get(sp)
            if binfo is not None:
                heapq.heappush(heap, (-_beff[sp], cnt, sp, m, binfo[2])); cnt += 1
                continue
            cinfo = _cmap.get(sp)
            if cinfo is not None:
                dc, nu_h = cinfo
                heapq.heappush(heap, (-_ceff[sp], cnt, sp, m * nu_h,
                                      ("__complex__", dc, nu_h))); cnt += 1
        if not heap:
            return None, he, ledger   # 无弱碱储备：直接返回原账本（避免无谓拷贝）
        ledger2 = dict(ledger)
        while heap and he > 0.0:
            neg_pka, _, base, m, acid = heapq.heappop(heap)
            pka = -neg_pka
            take = min(he, m)
            he -= take
            if isinstance(acid, tuple):
                # 配离子储备：按派生方程转化，不提供 Henderson 对、不再入堆
                _, dc, nu_h = acid
                dx = take / nu_h
                ledger2[base] = ledger2.get(base, 0.0) - dx
                for sp2, nu2 in dc.pr.items():
                    if sp2 != WATER:
                        ledger2[sp2] = ledger2.get(sp2, 0.0) + nu2 * dx
                continue
            b_rest = m - take
            hb = ledger2.get(acid, 0.0) + take
            ledger2[base] = b_rest
            ledger2[acid] = hb
            if b_rest > max(X_MIN, 1e-9 * V) and hb > 0.0:
                pH = pka + log10(b_rest / hb)
                return min(max(pH, -1.0), pKw + 1.0), he, ledger2
            nxt = _bases_map.get(acid) if multilevel else None
            # 产物仍是碱（可再质子化）→ 重新入堆（仅多级模式；单级模式与
            # 历史快照语义一致，pH 估计的全部既有行为不变）
            if nxt is not None and take > 0.0:
                heapq.heappush(heap, (-_beff[acid], cnt, acid, take,
                                      nxt[2])); cnt += 1
        return None, he, ledger2   # 全吸收（he≈0）→ 分支4；有残余 → 直读
    else:        # 弱酸吸收强碱：pKa 越小 Ka 越大，先中和
        he = -he
        heap = []
        cnt = 0
        for sp, m in ledger.items():
            if m <= X_MIN:
                continue
            ainfo = _acids_map.get(sp)
            if ainfo is None:
                continue
            peff = _aeff[sp]
            if peff > pKw + 2:
                continue   # 名义酸（NH3 pKa≈105）：水溶液中不可能给出质子
            heapq.heappush(heap, (peff, cnt, sp, m, ainfo[2])); cnt += 1
        if not heap:
            return None, -he, ledger
        ledger2 = dict(ledger)
        while heap and he > 0.0:
            pka, _, acid, m, base = heapq.heappop(heap)
            take = min(he, m)
            a_rest = m - take
            b = ledger2.get(base, 0.0) + take
            he -= take
            ledger2[acid] = a_rest
            ledger2[base] = b
            if a_rest > max(X_MIN, 1e-9 * V) and b > 0.0:
                pH = pka + log10(b / a_rest)
                return min(max(pH, -1.0), pKw + 1.0), -he, ledger2
            nxt = _acids_map.get(base) if multilevel else None
            # 产物仍是酸（可再去质子化）→ 重新入堆（仅多级模式）
            if nxt is not None and take > 0.0:
                heapq.heappush(heap, (_aeff[base], cnt, base, take,
                                      nxt[2])); cnt += 1
        return None, -he, ledger2


def estimate_pH(ledger: dict, H_excess: float, V: float, T, T_K: float) -> float:
    return estimate_state(ledger, H_excess, V, T, T_K)[0]


def estimate_state(ledger: dict, H_excess: float, V: float, T, T_K: float) -> tuple[float, dict, float]:
    """返回 (pH, 滴定后的虚拟账本, 残余He)。虚拟账本是 pH 一致的自由形态分布；
    残余He是弱酸/弱碱储备吸收后仍未中和的游离强酸/强碱（酸碱平衡后的真实 He）。"""
    pKw = pKw_of(T_K)
    # 1) 强酸连续形态分布后，游离 H+ 全部由 He 记账（分子分数不贡献游离 H+，
    #    不再有"分子态浓酸"直读分支——pH 即 -log10(自由 H+) 的自然结果）
    # 2) 缓冲滴定（质子条件近似）：游离强酸/强碱先被在账弱碱/弱酸储备按强度
    #    顺序吸收；被全吸收则由最后缓冲对的 Henderson 式定 pH；
    #    全吸收但无有效缓冲对 → 用残余 He（≈0）落分支 4，而非原 He 直读
    tit, He_res, ledger = _buffer_titration(ledger, H_excess, V, T, pKw, T_K=T_K)
    if tit is not None:
        return tit, ledger, He_res
    He = He_res / V
    if He >= 1e-3:
        return max(-1.0, -log10(He)), ledger, He_res
    if He <= -1e-3:
        return min(pKw + 1.0, pKw + log10(-He)), ledger, He_res
    # 4) 缓冲/弱酸弱碱区：取各来源贡献最大者（在滴定后的虚拟账本上评估）
    h_c = 10.0 ** (-pKw / 2)
    o_c = h_c

    def _pka1(e: dict) -> float:
        """该 pKa 条目在 T_K 的有效值（pKa=−logKa，van't Hoff 变号）。"""
        return e["pka"] - (_vant(e["dH"], T_K) if "dH" in e else 0.0)

    def _pkapp(e: dict) -> float:
        """每质子有效 pKa（多元酸/n>1 条目按 1/n 折算；对 pKa 变号）。"""
        return e["pka"] / e["n"] - (_vant(e["dH"] / e["n"], T_K) if "dH" in e else 0.0)

    def _pksp(e: dict) -> float:
        """pKsp(T)：pKsp = −logKsp，van't Hoff 变号。"""
        return e["pKsp"] - (_vant(e["dH"], T_K) if "dH" in e else 0.0)

    # 分支 4 的静态量（两性资格、各酸第一级 Ka、各碱最强共轭酸 pKa、
    # 金属水解 Kh、两性 pH）只依赖数据表与 T_K——按 T_K 缓存，避免每次
    # 调用全表重算（estimate_state 是 solve_extent 二分的最大热点）。
    # acids_map/bases_map/hyd_map 用 dict 而非 list，热路径改为遍历在账
    # 物种（~15 项）而非全表（~75 项），减少无物种命中的空转 get/除法。
    est_cache = getattr(T, "_est_static", None)
    if est_cache is None:
        est_cache = T._est_static = {}
    sc = est_cache.get(T_K)
    if sc is None:
        amph_eligible = set()
        amph_pH = {}
        for sp in set(T.pka_acid) & set(T.pka_base):
            e_a = min(T.pka_acid[sp], key=lambda e: e["pka"])
            e_bs = [e for e in T.pka_base[sp] if e["n"] == 1] or T.pka_base[sp]
            pka_b = max(_pkapp(e) for e in e_bs)
            if _pka1(e_a) <= pKw + 2 and pka_b <= pKw + 2:
                amph_eligible.add(sp)   # 如 HCO3-；NH3(pKa105)/HS-(pKa19) 不算
                amph_pH[sp] = (_pka1(e_a) + pka_b) / 2
        acids_map: dict = {}   # acid -> Ka 或 _STRONG_ACID 哨兵
        for acid, entries in T.pka_acid.items():
            if acid in amph_eligible:
                continue
            e1 = min(entries, key=lambda e: e["pka"])   # 第一级
            acids_map[acid] = _STRONG_ACID if e1["pka"] <= 0 else 10.0 ** (-_pka1(e1))
        bases_map: dict = {}   # base -> Kb
        for base, entries in T.pka_base.items():
            if base in T.solids or base == WATER or base in amph_eligible:
                continue
            e1s = [e for e in entries if e["n"] == 1] or entries
            pka = max(_pkapp(e) for e in e1s)           # 最强一级共轭酸
            bases_map[base] = 10.0 ** (pka - pKw)
        hyd_map: dict = {}    # cat -> Kh
        for e in T.ksp:
            cat, an = e["pair"]
            if an != "OH^-":
                continue
            hyd_map[cat] = 10.0 ** (_pksp(e) - charge_of(cat) * pKw)
        # 共轭酸碱对映射（缓冲对识别）：base -> (acid, pKa)，取最强一级
        conj: dict[str, tuple[str, float]] = {}
        for e in T.pka:
            if e.get("n", 1) != 1:
                continue
            b, a = e["base"], e["acid"]
            if b not in conj or _pkapp(e) > conj[b][1]:
                conj[b] = (a, _pkapp(e))
        sc = (amph_eligible, amph_pH, acids_map, bases_map, hyd_map, conj)
        est_cache[T_K] = sc
    amph_eligible, amph_pH, acids_map, bases_map, hyd_map, conj = sc
    # 单遍扫描在账物种：合并原 acids_l/bases_l/hyd_l/amph/buf 五个独立循环。
    # max/sum 可换序，h_c/o_c/amph/buf 的最终值与原实现等价。
    amph: list = []
    buf: list = []
    _floor_V = 1e-12 * V
    for sp, m in ledger.items():
        if m <= _floor_V:
            continue
        c = m / V
        Ka = acids_map.get(sp)
        if Ka is not None:
            if Ka is _STRONG_ACID:
                h_c = max(h_c, c)
            else:
                h_c = max(h_c, (-Ka + sqrt(Ka * Ka + 4 * Ka * c)) / 2)
        Kb = bases_map.get(sp)
        if Kb is not None:
            if Kb >= 1.0:                                # 水解近完全（S2-、C2^2- 等）
                o_c = max(o_c, c)
            else:
                o_c = max(o_c, (-Kb + sqrt(Kb * Kb + 4 * Kb * c)) / 2)
            # 共轭缓冲对（仅碱在账时检查其共轭酸是否也在账）
            if sp in conj:
                acid_conj, pka_c = conj[sp]
                ca = ledger.get(acid_conj, 0.0) / V
                if ca > 1e-12:
                    buf.append((pka_c + log10(c / ca), min(c, ca)))
            continue
        Kh = hyd_map.get(sp)
        if Kh is not None:
            h_c = max(h_c, (-Kh + sqrt(Kh * Kh + 4 * Kh * c)) / 2)
            continue
        if sp in amph_eligible and c > 1e-6 and sp not in T.solids:
            amph.append((c, amph_pH[sp]))
    # 两性物种（HCO3-、HS-、H2PO4- 等）：pH ≈ (pKa_酸 + pKa_共轭酸)/2，
    # 其浓度远大于其他酸碱贡献时以两性平衡为准（NaHCO3 溶液 pH≈8.3）
    if amph:
        c_a, pH_a = max(amph, key=lambda t: t[0])
        if c_a > 100.0 * max(h_c, o_c):
            return min(max(pH_a, -1.0), pKw + 1.0), ledger, He_res
    # 共轭缓冲对：弱碱与其共轭酸（或反之）同时在账时，pH 由
    # Henderson-Hasselbalch 决定（pKa + log(c_b/c_a)），sqrt(Kb·c) 的
    # 单物种估计会把缓冲体系误判为强碱性（NH4Ac 曾被估到 pH 10.4——
    # NH3 浓度稍高即触发；真实是 NH4+/NH3 与 Ac-/HAc 双缓冲 ≈7）。
    # 多对并存按弱组分浓度加权平均（双水解盐 → (pKa+pKa')/2 语义）
    if buf:
        w_sum = sum(w for _, w in buf)
        if w_sum > max(h_c, o_c):
            pH_buf = sum(p * w for p, w in buf) / w_sum
            return min(max(pH_buf, -1.0), pKw + 1.0), ledger, He_res
    pH = -log10(h_c) if h_c >= o_c else pKw + log10(o_c)
    return min(max(pH, -1.0), pKw + 1.0), ledger, He_res


# 分子态强酸的再平衡参数：酸 -> (共轭阴离子, 每分子释出质子数)
_RESPECIATE_ACIDS = {"HNO_3": ("NO_3^-", 1), "H_2SO_4": ("SO_4^{2-}", 2)}


def _respeciate_strong_acids(ledger: dict, H_excess: float, V: float, T) -> float:
    """分子态强酸 ⇌ 离子的逐轮再平衡：目标分子池 = f(c_tot)×库存，
    c_tot 由影子库存（"__tot_" 键，仅记本酸，盐类同离子不污染）给出。
    反应消耗游离 H+ 后分子池必须再电离，否则质子被锁死（Cu+4M HNO3
    停滞于 88%、Ba(OH)2+H2SO4 等量中和后残碱）；氧化剂通道消耗分子池/
    阴离子池后库存自然缩水，不会被无限回补。H2SO4 按 1:2 释出质子
    （全电离约定记账的表观近似；HSO4- 中间态依裁决不注册）。
    反应生成的酸（如 NO2 溶于水产生的 HNO3）无影子库存，不做分子化——
    稀区分子分数本来就 <1%，与全电离约定一致。返回修正后的 H_excess。"""
    He = H_excess
    for acid, (anion, n_H) in _RESPECIATE_ACIDS.items():
        shadow = ledger.get(f"__tot_{acid}", 0.0)
        if shadow <= 0.0:
            continue
        m_mol = ledger.get(acid, 0.0)
        m_an = ledger.get(anion, 0.0)
        # 游离酸库存：阴离子被金属离子聘为反离子（Cu(NO3)2）后不算"游离酸"——
        # 分子化曲线只对游离酸浓度有意义（否则 T78 停点时曲线反而把残余
        # 质子抽回分子池）。阴离子池被消耗时库存同步缩水。
        free_an = min(m_an, max(He, 0.0))
        avail = m_mol + min(free_an, max(shadow - m_mol, 0.0))
        tot = min(shadow, avail)
        ledger[f"__tot_{acid}"] = tot
        if tot <= 0.0:
            continue
        target = _mol_fraction(acid, tot / V, T) * tot
        d = target - m_mol          # >0 缔合（抽阴离子+质子）；<0 再电离
        if d > 0.0:
            d = min(d, m_an, He / n_H if He > 0.0 else 0.0)
        else:
            d = -min(-d, m_mol)
        if abs(d) <= X_MIN:
            continue
        ledger[acid] = m_mol + d
        ledger[anion] = m_an - d
        He -= n_H * d                 # 缔合(d>0)吸走质子，再电离(d<0)释放
    return He


def _full_speciation(ledger: dict, H_excess: float, V: float, T, T_K: float) -> tuple[dict, float]:
    """多级酸碱全形态分布 + 残余 He（仅供"惰性实现"判定）。与 estimate_state
    相同的分支结构，但滴定沿质子化梯走到底（VO3-→HVO3→VO2+ 一次调用完成），
    用于发现账本上不存在、却在当前酸度下真实存在的氧化还原物种。
    pH 估计不走此路——快照语义是 466 用例验证过的行为，两处各司其职。"""
    pKw = pKw_of(T_K)
    for acid in STRONG_MOLECULAR_ACIDS:
        # 分子态酸 ≥1M 才视为浓酸区制、跳过多级形态重排（连续形态分布下
        # 稀酸也有小量分子形态，不能一见分子就跳过——那是旧二元世界的语义）
        if ledger.get(acid, 0.0) / V >= 1.0:
            return ledger, H_excess
    _, He_res, vled = _buffer_titration(ledger, H_excess, V, T, pKw, multilevel=True,
                                        T_K=T_K)
    return vled, He_res


# ========================================================== 配平缓存

_BAL_CACHE: dict = {}


def _bal(reactants, products, free):
    key = (tuple(reactants), tuple(products), tuple(free))
    if key not in _BAL_CACHE:
        _BAL_CACHE[key] = balance(list(reactants), list(products), free=list(free))
    return _BAL_CACHE[key]


# ========================================================== 派生候选（§4.5：Hess 精确加和）

def build_derived(T) -> list[Cand]:
    if hasattr(T, "_derived"):
        return T._derived
    out: list[Cand] = []

    def add(kind, r, pr, logK, pkw_coeff=0.0, src="", dH=None):
        r = {k: v for k, v in r.items() if v}
        pr = {k: v for k, v in pr.items() if v}
        if not r or not pr:
            return
        # 静态需求集（除 H2O/H+）：§4.5 存在性过滤每迭代做子集判定，
        # 避免反复 genexpr/all 扫描（派生候选近千条，曾是最大热点之一）
        out.append(Cand(kind, r, pr, logK, pkw_coeff, dH=dH,
                        meta={"src": src,
                              "fwd_req": frozenset(s for s in r if s not in (WATER, H_ION)),
                              "rev_req": frozenset(s for s in pr if s not in (WATER, H_ION))}))

    def _hess_dH(*terms):
        """派生候选的 dH = 组元 dH 的同号 Hess 加和（组元缺数据→None 整体回退；
        水电离部分不进 dH，由 pkw_coeff 通道承载）。"""
        if any(t is None for t in terms):
            return None
        return sum(terms)

    for e in T.ksp:
        cat, an = e["pair"]
        solid, pK = e["solid"], e["pKsp"]
        # Ksp ⊗ β：solid + ν·ligand ⇌ complex + n_an·anion（logK = logβ − pKsp）
        # 计量系数由 _bal 现场配平给出，不预计算 n_cat/n_an
        for b in T.beta:
            if b["center"] != cat:
                continue
            bal = _bal([solid, b["ligand"]], [b["complex"], an], [WATER, H_ION])
            if bal is None:
                continue
            k_complex = bal["products"].get(b["complex"], 0)
            if k_complex <= 0:
                continue
            lg = b["logb"] * k_complex - pK * bal["reactants"][solid]
            r, pr = dict(bal["reactants"]), dict(bal["products"])
            # 总是正则化 OH-：配体为 OH- 或固体为氢氧化物时方程会产生 OH-
            coeff = _canonicalize_oh(r, pr)
            dh = _hess_dH(k_complex * b["dH"] if "dH" in b else None,
                          bal["reactants"][solid] * e["dH"] if "dH" in e else None)
            add("derived", r, pr, lg + coeff * PKW_298, coeff,
                f"ksp_beta:{solid}/{b['complex']}", dH=dh)
        # Ksp ⊗ pKa：solid + n·H+ ⇌ cation + 共轭酸（logK = pKa(和) − pKsp）
        for pe in T.pka_base.get(an, []):
            bal = _bal([solid, H_ION], [cat, pe["acid"]], [WATER])
            if bal is None:
                continue
            k_solid = bal["reactants"].get(solid, 0)
            if k_solid <= 0 or bal["reactants"].get(H_ION, 0) <= 0:
                continue
            n_acid = bal["products"].get(pe["acid"], 0)
            lg = pe["pka"] * n_acid - pK * k_solid
            r2, pr2 = dict(bal["reactants"]), dict(bal["products"])
            coeff2 = _canonicalize_oh(r2, pr2)
            dh = _hess_dH(-n_acid * pe["dH"] if "dH" in pe else None,
                          k_solid * e["dH"] if "dH" in e else None)
            add("derived", r2, pr2, lg + coeff2 * PKW_298, coeff2,
                f"ksp_pka:{solid}/{pe['acid']}", dH=dh)
        # Ksp ⊗ Ksp：solid1 + anion2 ⇌ solid2 + anion1（同阳离子，logK = pKsp1 − pKsp2）
        for e2 in T.ksp:
            if e2 is e or e2["pair"][0] != cat:
                continue
            an2 = e2["pair"][1]
            bal = _bal([solid, an2], [e2["solid"], an], [WATER])
            if bal is None:
                continue
            k1 = bal["reactants"].get(solid, 0)
            k2 = bal["products"].get(e2["solid"], 0)
            if k1 <= 0 or k2 <= 0:
                continue
            r3, pr3 = dict(bal["reactants"]), dict(bal["products"])
            coeff3 = _canonicalize_oh(r3, pr3)
            dh = _hess_dH(k1 * e["dH"] if "dH" in e else None,
                          -k2 * e2["dH"] if "dH" in e2 else None)
            add("derived", r3, pr3,
                -pK * k1 + e2["pKsp"] * k2 + coeff3 * PKW_298, coeff3,
                f"ksp_ksp:{solid}->{e2['solid']}", dH=dh)

    # β ⊗ pKa：complex + ν·H+ ⇌ center + ν·共轭酸（logK = ν·pKa − logβ）
    for b in T.beta:
        lig = b["ligand"]
        if lig == "OH^-":
            lg = b["nu"] * PKW_298 - b["logb"]
            bal = _bal([b["complex"], H_ION], [b["center"], WATER], [WATER])
            if bal is None or bal["reactants"].get(H_ION, 0) <= 0:
                continue
            out.append(Cand("derived", dict(bal["reactants"]), dict(bal["products"]),
                            lg, b["nu"], dH=-b["dH"] if "dH" in b else None,
                            meta={"src": f"beta_pka:{b['complex']}"}))
        else:
            for pe in T.pka_base.get(lig, []):
                if pe["n"] != 1:
                    continue
                lg = b["nu"] * pe["pka"] - b["logb"]
                bal = _bal([b["complex"], H_ION], [b["center"], pe["acid"]], [WATER])
                if bal is None or bal["reactants"].get(H_ION, 0) <= 0:
                    continue
                k_acid = bal["products"].get(pe["acid"], 0)
                kc = bal["reactants"][b["complex"]]
                dh = _hess_dH(-k_acid * pe["dH"] if "dH" in pe else None,
                              -kc * b["dH"] if "dH" in b else None)
                out.append(Cand("derived", dict(bal["reactants"]), dict(bal["products"]),
                                pe["pka"] * k_acid - b["logb"] * kc,
                                0.0, dH=dh, meta={"src": f"beta_pka:{b['complex']}"}))

    # Ksp⊗弱酸（酸为反应物）：solid + HA -> cat + 共轭碱（弱酸溶蚀沉淀，
    # 如 CaCO3 + CO2 + H2O -> Ca2+ + 2HCO3-，钟乳石/暂时硬水；强酸溶解由
    # ksp_pka 处理，此处只收比阴离子共轭酸更弱的 HA）
    for e in T.ksp:
        solid, cat, an, pK = e["solid"], e["pair"][0], e["pair"][1], e["pKsp"]
        if an == "OH^-":
            continue
        for pe_an in T.pka_base.get(an, []):
            if pe_an["n"] != 1:
                continue
            h_an, pka_hi = pe_an["acid"], pe_an["pka"]
            if h_an == WATER:
                continue
            for ha, entries in T.pka_acid.items():
                if ha in T.solids or ha == WATER:
                    continue
                e1 = min(entries, key=lambda x: x["pka"])
                if e1["pka"] <= 0 or e1["pka"] >= pka_hi:
                    continue
                bal = _bal([solid, ha], [cat, h_an, e1["base"]], [WATER, H_ION])
                if bal is None or H_ION in bal["reactants"] or H_ION in bal["products"]:
                    continue
                k_s = bal["reactants"].get(solid, 0)
                k_ha = bal["reactants"].get(ha, 0)
                if k_s <= 0 or k_ha <= 0:
                    continue
                lg = -pK * k_s + (pka_hi - e1["pka"]) * k_ha
                dh = _hess_dH(k_s * e["dH"] if "dH" in e else None,
                              -k_ha * pe_an["dH"] if "dH" in pe_an else None,
                              k_ha * e1["dH"] if "dH" in e1 else None)
                add("derived", dict(bal["reactants"]), dict(bal["products"]), lg, 0.0,
                    f"ksp_acid:{solid}/{ha}", dH=dh)

    # β_pka ⊗ Ksp(氢氧化物)：complex + (ν/n−1)·center + ν·H2O -> (ν/n)·solid + ν·共轭酸
    # （H+ 抵消形派生：沉淀-解配耦合通道，如 [Cu(NH3)4]2+ + Cu2+ + 4H2O ->
    #   2Cu(OH)2 + 4NH4+。两单独候选各自被 pH 反馈锁死，组合通道直接可达平衡）
    for b in T.beta:
        if b["ligand"] == "OH^-":
            continue
        cell = T.ksp_by_pair.get((b["center"], "OH^-"))
        if cell is None:
            continue
        n_OH = charge_of(b["center"])
        for pe in T.pka_base.get(b["ligand"], []):
            if pe["n"] != 1:
                continue
            bal = _bal([b["complex"], b["center"]], [cell["solid"], pe["acid"]],
                       [WATER, H_ION])
            if bal is None:
                continue
            if H_ION in bal["reactants"] or H_ION in bal["products"]:
                continue   # 只保留 H+ 抵消形
            kc = bal["reactants"].get(b["complex"], 0)
            ks = bal["products"].get(cell["solid"], 0)
            ka = bal["products"].get(pe["acid"], 0)
            if kc <= 0 or ks <= 0 or ka <= 0:
                continue
            lg = (kc * (b["nu"] * pe["pka"] - b["logb"])
                  + ks * (cell["pKsp"] - n_OH * PKW_298))
            dh = _hess_dH(-kc * b["nu"] * pe["dH"] if "dH" in pe else None,
                          -kc * b["dH"] if "dH" in b else None,
                          -ks * cell["dH"] if "dH" in cell else None)
            add("derived", dict(bal["reactants"]), dict(bal["products"]),
                lg, -ks * n_OH, f"beta_ksp:{b['complex']}/{cell['solid']}", dH=dh)

    # 去重
    seen: dict = {}
    for c in out:
        seen.setdefault(c.key, c)
    T._derived = list(seen.values())
    return T._derived


def _canonicalize_oh(r: dict, pr: dict) -> float:
    """把方程中的 OH- 改写为 H2O/H+ 正则形；返回 pKw 系数（logK 修正）。"""
    coeff = 0.0
    k = r.pop("OH^-", 0)
    if k:
        r[WATER] = r.get(WATER, 0) + k
        pr[H_ION] = pr.get(H_ION, 0) + k
        coeff -= k          # logK -= k·pKw
    k = pr.pop("OH^-", 0)
    if k:
        pr[WATER] = pr.get(WATER, 0) + k
        r[H_ION] = r.get(H_ION, 0) + k
        coeff += k          # logK += k·pKw
    for d in (r, pr):
        if d.get(WATER, 0) == 0:
            d.pop(WATER, None)
    return coeff


# ========================================================== 候选枚举

def _redox_templates(T_K: float, T) -> list:
    """(T, T_K) 下 redox 候选的静态模板，按 T_K 字典缓存于 T._redox_tmpl。

    主循环是不动点迭代：电对配对、配平、电子数折算、慢标记、温度闸门在
    同一 (T, T_K) 下是"定值"，只算一次；存在性/pH 闸门/产物形态随迭代
    动态过滤（见 enumerate_candidates §4.1）。模板顺序保持原 (a, b) 嵌套
    顺序以维持候选序列（tie-break 依赖枚举顺序）。

    模板项：(a, b, variants, dE, slow, slow_dirs, acid_limited)
    variants：[(a_ox, r, pr, n)]——常规电对单变体；NO3- 电对附加 HNO3
    分子态变体（浓硝酸），运行时按存在性二选一。

    缓存改为 dict{T_K: (tmpls, by_aox)}——多温度场景（如 298K↔350K 交替）
    不再每次重建；同 T_K 重复调用零成本。
    """
    cache = getattr(T, "_redox_tmpl", None)
    if cache is None:
        cache = T._redox_tmpl = {}
    hit = cache.get(T_K)
    if hit is not None:
        return hit
    # 固相电对（ox 为固相氧化物/单质）的还原侧 **阳离子/中性** 物种受酸限量约束；
    # 阴离子还原剂（I-/S2- 等）不应被误纳——它们在碱中经水形式照常反应
    solid_metals = {c["red"] for c in T.couples
                    if c["ox"] in T.solids and charge_of(c["red"]) >= 0}
    tmpls = []
    for a in T.couples:
        if a.get("ox_inert"):
            continue
        g = a.get("gate") or {}
        # T_min（无 only_vs_red）为静态温度闸门，模板构建时过滤
        if "T_min" in g and "only_vs_red" not in g and T_K < g["T_min"]:
            continue
        for b in T.couples:
            if b is a:
                continue
            if not _vs_gate_ok(a, b, T_K, T):
                continue
            # 共享氧化形或还原形的电对配对：电子 bookkeeping 净零，净反应实为
            # 酸碱/形态转换（如 HClO/Cl- ⊗ ClO-/Cl- 净得 HClO->ClO-+H+），
            # 属 pKa 模块管辖，作为 redox 枚举会产生幻影自发通道
            if a["red"] == b["red"] or a["ox"] == b["ox"]:
                continue
            slow = _slow_flag(a, b, T_K, T)
            # 卤素歧化到卤酸根方向感知的慢标记（教材规则：冷稀碱 -> ClO-，热碱
            # -> ClO3-；歧化经 XO- 中间体，其进一步歧化室温慢——漂白液室温稳定
            # 即此动力学事实；Br/I 为碱催化解锁，见 HALATE_DISP_T/HALATE_BASE_PH）。
            # 同一平衡有两种枚举取向：歧化取向正向慢、归中取向逆向慢；
            # 归中方向（IO3-+I-）与异种氧化（Cl2+I2 -> IO3-）始终快。
            # sd_static：构建期可判定的慢方向；sd_ph：碱催化解锁的慢方向
            # （pH < 阈值才慢，pH 随迭代变，运行时在枚举动态段判定）
            sd_static = frozenset()
            sd_ph: list = []
            for _d, _hx in ((1, b["ox"]), (-1, a["ox"])):
                _src_ok = (a["ox"] == b["red"]) if _d == 1 else (b["ox"] == a["red"])
                _t = HALATE_DISP_T.get(_hx)
                if _src_ok and _t is not None and T_K < _t:
                    _ph = HALATE_BASE_PH.get(_hx)
                    if _ph is None:
                        sd_static |= {_d}
                    else:
                        sd_ph.append((_d, _ph))
            # NO3- 电对双变体：离子态 / 浓硝酸分子态（运行时按存在性选择）
            variants = []
            for a_ox in ([a["ox"], "HNO_3"] if a["ox"] == "NO_3^-" else [a["ox"]]):
                bal = _bal([a_ox, b["red"]], [a["red"], b["ox"]], [WATER, H_ION])
                if bal is None:
                    # 配合物电对的配体参与配平（[AuCl4]-/Au 经王水溶解：
                    # Au + 4Cl- -> [AuCl4]- + 3e，Cl- 必须可入账）；先按常规
                    # 池配平，失败才补配体——不影响既有模板
                    lig = []
                    for sp in (a["ox"], a["red"], b["ox"], b["red"]):
                        be = T.beta_by_complex.get(sp)
                        if be and be["ligand"] not in lig:
                            lig.append(be["ligand"])
                    if lig:
                        bal = _bal([a_ox, b["red"]], [a["red"], b["ox"]],
                                   [WATER, H_ION] + lig)
                if bal is None:
                    continue
                r, pr = dict(bal["reactants"]), dict(bal["products"])
                # 实际转移电子数（配平已约简，lcm 会高估，如 I2 歧化 n_a=2/n_b=5
                # 的 3I2 方程实际转 5e 而非 lcm=10）：按半反应系数折算；
                # 归中体系产物侧物种共享，须从反应物侧折算。
                # 氧化还原+沉淀/酸碱合并的混合候选中系数含旁观计量（如
                # 2Fe(OH)3+3Fe2+ -> 3Fe(OH)2+2Fe3+ 实际只转 2e，1 个 Fe2+ 仅沉淀），
                # 任一单物种折算都是电子数的上界 → 取四向折算的最小正值（最紧上界）
                _opts = []
                if a["red"] != b["ox"]:
                    _opts.append((_half_scale(a), pr.get(a["red"], 0)))
                _opts.append((_half_scale(b), r.get(b["red"], 0)))
                _opts.append((a["n"], r.get(a_ox, 0)))
                _opts.append((b["n"], pr.get(b["ox"], 0)))
                _vals = [sc * nu for sc, nu in _opts if nu]
                n = min(_vals) if _vals else 0
                if n <= 0:
                    continue
                if H_ION in r and H_ION in pr:
                    continue
                # van't Hoff：全反应 ΔH = ν_a·dH_a − ν_b·dH_b（ν=n/dH_n 为半反应
                # 折算系数；H+ 的 ΔHf 约定为 0，H+ 项自动含于半反应 dH）。
                # 任一电对缺 dH → None，回退 Nernst k(T) 缩放（ΔS≈0 近似）。
                dH_full = None
                if "dH" in a and "dH" in b:
                    dH_full = n / a["dH_n"] * a["dH"] - n / b["dH_n"] * b["dH"]
                variants.append((a_ox, r, pr, n, dH_full))
            if variants:
                tmpls.append((a, b, variants, a["E0"] - b["E0"], slow,
                              sd_static, sd_ph, b["red"] in solid_metals))
    # a_ox -> 模板下标：每迭代只检查氧化形在账的模板（原实现每迭代全表扫
    # 数千模板，反而慢于旧的双重循环）；下标排序后遍历保持原 (a,b) 嵌套序
    by_aox: dict = {}
    for i, tpl in enumerate(tmpls):
        by_aox.setdefault(tpl[0]["ox"], []).append(i)
    T._redox_tmpl[T_K] = (tmpls, by_aox)
    return tmpls, by_aox


def enumerate_candidates(ledger: dict, H_excess: float, pH: float, V: float, T_K: float, T,
                         gate_ctx: dict | None = None) -> list[Cand]:
    pKw = pKw_of(T_K)
    aOH = 10.0 ** (pH - pKw)
    # present 含 H_ION（始终在账，由 H_excess 记账）与 WATER（溶剂），
    # 使原 _species_present(sp, present) 简化为 sp in present（4.2M 次调用消除）。
    # subset 判定 req <= present 不受影响：req 已排除 (WATER, H_ION)。
    present = {s for s, m in ledger.items() if m > X_MIN}
    present.add(H_ION)
    out: list[Cand] = []

    # ---- 4.1 电子转移
    # 静态部分（电对配对/配平/电子数/慢标记/温度闸门）由 _redox_templates 按
    # (T, T_K) 缓存——主循环是不动点迭代，每迭代重建候选时这些"定值"不再重算；
    # 存在性、pH/浓度闸门、产物形态规则随状态变化，每迭代动态过滤。
    # 按 a_ox 索引预取在账氧化形的模板，下标排序后遍历保持原 (a,b) 嵌套序
    tmpls, by_aox = _redox_templates(T_K, T)
    idxs = []
    for k, ii in by_aox.items():
        if k in present:
            idxs.extend(ii)
        # 浓硝酸分子态：NO3- 缺席时以 HNO3 分子充当氧化剂
        elif k == "NO_3^-" and ledger.get("HNO_3", 0.0) / V >= 1.0:
            idxs.extend(ii)
    idxs.sort()
    for i in idxs:
        a, b, variants, dE, slow, sd_static, sd_ph, acid_limited = tmpls[i]
        a_ox = a["ox"]
        if a_ox not in present:
            a_ox = "HNO_3"   # 到达此处说明 NO3- 缺席且 HNO3 浓（见上预取）
        # gate（pH_max/c_min/c_max）描述"该电对作为氧化剂"的可及性，
        # 只对 a（氧化剂）侧生效；b 为还原剂侧，其 ox 是产物，不应被闸门拦截
        # （例：NO2 溶于水被氧化为 NO3- 不要求 pH≤1.5）
        # 内联 _couple_gate_dyn -> _gate_check(a.get("gate"), ...) 消除 1.9M 次调用
        if not _gate_check(a.get("gate"), a, ledger, pH, V, T):
            continue
        if b["red"] not in present:
            continue
        # 固相形态规则仅适用于溶剂/酸背景（a 为 H+）；强氧化剂在场时
        # 金属被氧化为游离离子是正常的（例：Cu+AgNO3 -> Cu2+ + Ag）
        if a_ox == H_ION and not _product_form_ok(b, aOH, T, T_K):
            continue
        # b 侧还原通道 pH 下限：如 Mn2+ 氧化为 MnO2 实际经 Mn(OH)2，
        # 酸性中该方向动力学/形态上不可达
        if pH < b.get("red_pH_min", -1e9):
            continue
        # b 侧电对被逆向驱动（red 消耗、ox 生成）时的可选反向闸门 rev_gate：
        # 如 NO2 作还原剂被氧化回 NO3-，仅当残余游离酸稀薄——浓介质中生成
        # 的 NO2 以气体逸出，不被 Hg2+ 等边际氧化剂（ΔE≈0.05V）回氧化，
        # 与歧化支路同一"逸出气体"粗粒化（c_basis shadow，1M 阈值）
        rg = b.get("rev_gate")
        if rg is not None and not _gate_check(rg, b, ledger, pH, V, T):
            continue
        var = None
        for v in variants:
            if v[0] == a_ox:
                var = v
                break
        if var is None:
            continue
        _, r, pr, n, dH_full = var
        # 碱催化解锁的卤素歧化慢方向：pH 低于阈值才标记慢（碘量法窗口 pH<8、
        # 强碱中 I2/Br2 歧化至卤酸根为快反应）
        slow_dirs = sd_static
        for d, ph in sd_ph:
            if pH < ph:
                slow_dirs |= {d}
        # 有固相电对的金属受酸限量约束（耗尽后走固相/膜路径）；
        # 无固相电对的金属（Na/Ca/K）水直接氧化，不受限
        out.append(Cand("redox", r, pr, 0.0, 0.0,
                        redox=(n, dE), dH=dH_full,
                        meta={"slow": slow, "slow_dirs": slow_dirs,
                              "ox_couple": a["ox"],
                              "acid_limited": acid_limited}))

    # ---- 4.2 质子转移（pKa ≤ 0 强酸条目不参与，规范化已拆解）
    # 配平与 H+ 挂侧检查静态（只依赖数据表），缓存于 T；存在性每迭代过滤
    if getattr(T, "_proton_static", None) is None:
        ps = []
        for e in T.pka:
            if e["pka"] <= 0:
                continue
            acid, base = e["acid"], e["base"]
            bd = _bal([acid], [base, H_ION], [WATER])
            diss = (dict(bd["reactants"]), dict(bd["products"])) \
                if bd is not None and bd["products"].get(H_ION, 0) > 0 else None
            bp = _bal([base, H_ION], [acid], [WATER])
            prot = (dict(bp["reactants"]), dict(bp["products"])) \
                if bp is not None and bp["reactants"].get(H_ION, 0) > 0 else None
            ps.append((acid, base, e["pka"], diss, prot, e.get("dH")))
        T._proton_static = ps
    for acid, base, pka, diss, prot, dH_ion in T._proton_static:
        if diss is not None and acid in present:
            out.append(Cand("proton", diss[0], diss[1], -pka, dH=dH_ion,
                            meta={"dir": "diss"}))
        if prot is not None and base in present:
            out.append(Cand("proton", prot[0], prot[1], pka,
                            dH=-dH_ion if dH_ion is not None else None,
                            meta={"dir": "prot"}))

    # ---- 4.3 沉淀 / 溶解（氢氧化物用 H+ 正则形，与阳离子水解同一平衡）
    for e in T.ksp:
        cat, an = e["pair"]
        solid, pK = e["solid"], e["pKsp"]
        qc, qa = charge_of(cat), -charge_of(an)
        g = gcd(qc, qa)
        n_cat, n_an = qa // g, qc // g
        # dH 不含水电离部分（OH- 型固体的水电离项由 pkw_coeff 通道承载），
        # 沉淀/溶解均直接取 ∓溶解焓
        dH_sol = e.get("dH")
        dH_p = -dH_sol if dH_sol is not None else None
        if an == "OH^-":
            if cat in present:
                out.append(Cand("precip", {cat: n_cat, WATER: n_an}, {solid: 1, H_ION: n_an},
                                pK - n_an * pKw, -n_an, dH=dH_p, meta={"solid": solid}))
            if solid in present:
                out.append(Cand("dissolve", {solid: 1, H_ION: n_an}, {cat: n_cat, WATER: n_an},
                                n_an * pKw - pK, n_an, dH=dH_sol, meta={"solid": solid}))
        else:
            if cat in present and an in present:
                r: dict = {cat: n_cat}
                if n_an:
                    r[an] = n_an
                out.append(Cand("precip", r, {solid: 1}, pK, dH=dH_p, meta={"solid": solid}))
            if solid in present:
                pr: dict = {cat: n_cat}
                if n_an:
                    pr[an] = n_an
                out.append(Cand("dissolve", {solid: 1}, pr, -pK, dH=dH_sol, meta={"solid": solid}))

    # ---- 4.4 配位 / 解离（OH- 配体正则化）
    for b in T.beta:
        center, lig, comp = b["center"], b["ligand"], b["complex"]
        nu = b["nu"]
        dH_b = b.get("dH")
        dH_nb = -dH_b if dH_b is not None else None
        if lig == "OH^-":
            if center in present:
                out.append(Cand("complex", {center: 1, WATER: nu}, {comp: 1, H_ION: nu},
                                b["logb"] - nu * pKw, -nu, dH=dH_b))
            if comp in present:
                out.append(Cand("decomplex", {comp: 1, H_ION: nu}, {center: 1, WATER: nu},
                                nu * pKw - b["logb"], nu, dH=dH_nb))
        else:
            if center in present and lig in present:
                out.append(Cand("complex", {center: 1, lig: nu}, {comp: 1}, b["logb"],
                                dH=dH_b))
            if comp in present:
                out.append(Cand("decomplex", {comp: 1}, {center: 1, lig: nu}, -b["logb"],
                                dH=dH_nb))

    # ---- 4.5 Hess 派生候选（双向查在账：正向或反向均可参与）
    for c in build_derived(T):
        req = c.meta.get("fwd_req")
        if req is None:   # 直接构造（非 add()）的派生候选：惰性补算，写一次
            req = c.meta["fwd_req"] = frozenset(
                s for s in c.r if s not in (WATER, H_ION))
            c.meta["rev_req"] = frozenset(
                s for s in c.pr if s not in (WATER, H_ION))
        if req <= present or c.meta["rev_req"] <= present:
            out.append(c)
    return out


def _species_present(sp: str, present: set) -> bool:
    return sp == H_ION or sp == WATER or sp in present


def _gate_check(g: dict | None, c: dict, ledger: dict, pH: float, V: float, T) -> bool:
    """单个闸门字典的动态检查（pH/浓度随迭代变化；T_min 静态部分在模板
    构建时过滤）。浓度闸门逐轮动态（真实化学：反应消耗酸使介质由浓转稀，
    氧化剂通道随之中途切换——T77/T78 同为 8M HNO3，仅凭 3Cu 耗尽酸库转稀
    才给出 NO）。"c_basis": "shadow" 时改读游离酸影子库存（仅本酸、不含
    盐类同离子）：用于 NO2 的两条回氧支路（歧化、金属离子回氧化）——
    3NO2+H2O->2HNO3+NO 是"NO2 通入水"的吸收平衡，被产物游离硝酸
    Le Chatelier 抑制；残余游离酸达摩尔量级时生成的 NO2 以气体逸出不被
    回吸/回氧（引擎不建气相的粗粒化，实际裕度 4M vs 0M，阈值宽区间不
    敏感）。反应生成的酸无影子库存（读作 0）→ NO2 通入纯水通道始终开。
    g 为 None 或空字典视为无闸门，避免上层 1.9M 次 `c.get('gate') or {}`
    的临时字典分配。"""
    if not g:
        return True
    if "pH_max" in g and pH > g["pH_max"]:
        return False
    if "pH_min" in g and pH < g["pH_min"]:
        return False
    if "c_min" in g or "c_max" in g:
        acid = g.get("acid", c["ox"])
        if g.get("c_basis") == "shadow":
            conc = ledger.get(f"__tot_{acid}", 0.0) / V
        else:
            conc = _acid_conc(acid, ledger, V, T)
        if "c_min" in g and conc < g["c_min"]:
            return False
        if "c_max" in g and conc >= g["c_max"]:
            return False
    return True


def _couple_gate_dyn(c: dict, ledger: dict, pH: float, V: float, T,
                     gate_ctx: dict | None = None) -> bool:
    """电对正向（作为氧化剂）闸门的动态部分；gate_ctx 保留兼容，已无使用。"""
    return _gate_check(c.get("gate"), c, ledger, pH, V, T)


def _vs_gate_ok(a: dict, b: dict, T_K: float, T=None) -> bool:
    """配对级闸门（静态，模板构建期判定）：
    only_vs_red：该电对的 T_min 闸门只对指定还原剂生效（MnO2 对 Cl- 需加热）；
    vs_red_only / vs_red_block：还原剂白/黑名单（旧式，保留兼容）；
    vs_red_E_max / vs_red_block_E_max：硝酸还原产物选择性的原理化规则——
    "金属越活泼，产物价态越低"是动力学事实，但判据是还原剂电对的 E0
    （连续量）而非金属名单：E0(还原剂电对) ≤ 阈值才开放/关闭。任何金属
    （包括未列名的 Ca/V/Cr…）由此自然归类，无需逐一注册。
    阈值取值：N2O 通道 -0.6V（Zn -0.76 开 / Fe -0.44 关）、NH4+ 通道
    -1.0V（Zn 开 / Al -1.68 关）——均由教材已知成员列表反解。"""
    for c, partner in ((a, b), (b, a)):
        g = c.get("gate")
        if not g:
            continue
        if "vs_red_only" in g and partner["red"] not in g["vs_red_only"]:
            return False
        if "vs_red_block" in g and partner["red"] in g["vs_red_block"]:
            return False
        if T is not None and "vs_red_E_max" in g:
            e_red = T.redox_red_E.get(partner["red"])
            if e_red is None or e_red > g["vs_red_E_max"]:
                return False
        if T is not None and "vs_red_block_E_max" in g:
            e_red = T.redox_red_E.get(partner["red"])
            if e_red is not None and e_red <= g["vs_red_block_E_max"]:
                return False
        if "T_min" in g and "only_vs_red" in g and T_K < g["T_min"]:
            if partner["red"] in g["only_vs_red"]:
                return False
    return True


def _acid_conc(acid: str, ledger: dict, V: float, T) -> float:
    """分子态浓酸的浓度（浓 H2SO4 / 浓 HNO3）；非分子形态返回 0。"""
    ex = T.ex.get(acid)
    if not ex:
        return 0.0
    cf = ex.get("conc_forms")
    if cf and cf.get("concentrated") == "molecule":
        return ledger.get(acid, 0.0) / V
    return 0.0


def _slow_flag(a: dict, b: dict, T_K: float, T) -> bool:
    # 固相 -> 阳离子映射挂到 T 上缓存（原模块级全局在换数据表时会串表）
    ksp_cat = getattr(T, "_ksp_cat", None)
    if ksp_cat is None:
        ksp_cat = {e["solid"]: e["pair"][0] for e in T.ksp}
        T._ksp_cat = ksp_cat
    for c, other, is_a in ((a, b, True), (b, a, False)):
        # 指定还原剂组合恒慢（动力学，与温度无关）：如 H2O2 歧化
        if other["red"] in c.get("slow_with_red", []):
            return True
        # 该电对作还原剂侧（其 red 被氧化）恒慢：如水被阳极氧化为 H2O2
        if not is_a and c.get("slow_as_reductant"):
            return True
        # 指定氧化剂组合恒慢：如 SO3^2- 还原 H+/水析 H2（亚硫酸盐溶液动力学稳定）
        if other["ox"] in c.get("slow_with_ox", []):
            return True
        # S(IV)（SO2/亚硫酸）作还原剂把金属离子还原为金属单质：水溶液中动力学
        # 封闭——SO2 还原只到中间价态（如 Cu2+->Cu+），从不在水溶液析出金属
        if (not is_a and c["red"] == "SO_2" and other["red"] in T.solids
                and other["red"] not in NONMETAL_SOLIDS):
            return True
        # H2 作还原剂在水溶液中常温恒慢（需催化剂或高温加热才表现还原性，
        # 如 H2 还原 CuO 需加热；水溶液中 H2 不还原 Cu2+/Fe3+ 等）
        if not is_a and c["red"] == "H_2":
            return True
        # 活泼金属（Li/Na/K/Rb/Cs/Ca/Sr/Ba）在水溶液中优先还原水而非金属物种
        # （教材规则：钠投入盐溶液只与水反应，再生成氢氧化物沉淀；Be/Mg 可直接置换）。
        # 覆盖两类氧化剂形态：金属阳离子、含金属阳离子的固相氢氧化物/氧化物
        if (not is_a and c["red"] in WATER_FIRST_METALS
                and other["ox"] != H_ION
                and (charge_of(other["ox"]) > 0
                     or (other["ox"] in ksp_cat and charge_of(ksp_cat[other["ox"]]) > 0))):
            return True
        sb = c.get("slow_below")
        if not sb:
            continue
        # O2 析出（该电对作 b 方、O2 为产物 = 水被氧化）：4e- 阳极过程，
        # 温度不解锁（与 O2 作氧化剂的 slow_below 区分开）
        if c["ox"] == "O_2" and not is_a:
            return True
        if T_K < sb and other["red"] not in c.get("slow_except_red", []):
            return True
    return False


def _product_form_ok(b: dict, aOH: float, T, T_K: float = 298.15) -> bool:
    """固相形态规则（§4.6）：b.ox 为游离金属离子、存在同金属固相电对、
    且当前 pH 下其氢氧化物饱和活度低于 SAT_SKIP 时，跳过裸离子路径。"""
    ox = b["ox"]
    cell = T.ksp_by_pair.get((ox, "OH^-"))
    if cell is None:
        return True
    solid = cell["solid"]
    if not any(c["ox"] == solid and c["red"] == b["red"] for c in T.couples):
        return True
    n_OH = charge_of(ox)
    pksp = cell["pKsp"] - (_vant(cell["dH"], T_K) if "dH" in cell else 0.0)
    a_sat = 10.0 ** (-pksp) / aOH ** n_OH
    return a_sat > SAT_SKIP


# ========================================================== ③ S = logK − logQ

def S_of(c: Cand, ledger: dict, V: float, pH: float, T_K: float, T,
         gsup: frozenset = frozenset(), p_ext_kpa: float = P_EXT_KPA) -> float:
    """计算候选反应的亲和势 S = logK − logQ。

    p_ext_kpa: 外界气相总压（kPa）。低于常压时气体更易逸出（泡点降低），
    高于常压时更多气体留在溶液。默认 P_EXT_KPA（101.3 kPa 常压）。
    """
    logQ = 0.0
    for s, nu in c.r.items():
        if s == H_ION:
            logQ += nu * pH
        elif s == WATER or s in T.solids:
            pass
        else:
            logQ -= nu * log10(max(ledger.get(s, 0.0) / V, ACT_FLOOR))
    for s, nu in c.pr.items():
        if s == H_ION:
            logQ -= nu * pH
        elif s == WATER or s in T.solids:
            pass
        elif s in T.gases:
            # 气体产物活度（p/p°，Henry 定律 p=c/H）：
            # 外加供给的气体按账本浓度（溶质活度，持续供给维持）；
            # 自产气体在惰性环境逸出——分压下限 P_RES（残余扫气）、
            # 上限 p_ext_kpa（纯气体鼓泡），其间按 Henry 分压 c/(H·p°) 插值；
            # 无 Henry 数据的物种回退固定 A_GAS
            if s in gsup:
                logQ += nu * log10(max(ledger.get(s, 0.0) / V, ACT_FLOOR))
            else:
                # 泡点判据：溶解气体平衡分压 p=c/H 超过外界总压才鼓泡逸出
                # （a=A_GAS）；低于泡点在反应时标内以溶质形态留在溶液
                # （a=c/c°）——常压惰性环境下稀释可溶气体不强制脱气
                H = T.henry.get(s)
                c_g = ledger.get(s, 0.0) / V
                if H is not None and c_g < H * p_ext_kpa:
                    a = max(c_g, A_GAS)   # 残余分压下限（c→0 时与旧约定一致，
                                          # 避免 1e-12 地板制造虚假驱动）
                else:
                    a = A_GAS
                logQ += nu * log10(a)
        else:
            logQ += nu * log10(max(ledger.get(s, 0.0) / V, ACT_FLOOR))
    return logK_T(c, T_K) - logQ


# ========================================================== ④ 平衡程度求解

def solve_extent(c: Cand, direction: int, ledger: dict, H_excess: float,
                 V: float, T_K: float, T,
                 gsup: frozenset = frozenset(), iters: int = 60,
                 p_ext_kpa: float = P_EXT_KPA) -> tuple[float, float]:
    """返回 (x*, x_max)。x* 为使 S 归零的程度；若全程为正则取计量上限。

    p_ext_kpa 透传给 S_of 用于气体泡点判据（外界气压调节）。"""
    rr = c.r if direction > 0 else c.pr
    pp = c.pr if direction > 0 else c.r
    limited = [(s, nu) for s, nu in rr.items() if s not in (WATER, H_ION)]
    if not limited:
        return 0.0, 0.0
    x_max = min(ledger.get(s, 0.0) / nu for s, nu in limited)
    if x_max <= X_MIN:
        return 0.0, x_max
    nu_H = pp.get(H_ION, 0) - rr.get(H_ION, 0)
    if c.kind == "redox":
        # 游离强酸/强碱为定量资源：redox 净耗 H+ 受 H_excess 限制（耗尽后由
        # 固相/水氧化路径另行处理）；净产 H+（耗 OH-）受碱储备限制。
        # 例外：无固相电对的还原剂（Na/Ca/K/Fe2+/I- 等）——水供质子，
        # 耗 H+ 升 pH 由 f(x) 的 S 归零自限，整个 nu_H<0 分支都不受游离酸
        # 硬约束（原仅在 He<=0 时豁免，He 为微小正残差时仍被误顶死）
        if nu_H < 0:
            if c.meta.get("acid_limited", True):
                if H_excess <= 0.0:
                    return 0.0, 0.0
                x_max = min(x_max, H_excess / (-nu_H))
        elif nu_H > 0 and H_excess < 0.0:
            # 净产 H+ ≡ 耗 OH-：仅当体系确实呈碱性（有真实 OH- 储备）才限量。
            # 酸性/近中性时 He 的微小负残差是记账幻影（speciation 把质子编入
            # HNO2 等弱酸形态，estimate_pH 仍报酸），真实 OH- 储备 ~1e-10、
            # 无实际限量对象——S 随 pH 下降自限。否则 NO2 歧化类产酸通道被
            # 幻影残差顶成每轮 -He/nu_H 的微步爬行（T78 曾 1500 轮 6.6s）
            if estimate_pH(ledger, H_excess, V, T, T_K) > 9.0:
                x_max = min(x_max, -H_excess / nu_H)
        if x_max <= X_MIN:
            return 0.0, x_max

    # 预计算变化物种及其每单位 x 的净增量（rr 消耗为负、pp 生成为正）。
    # 原实现每次 f(x) 调用都 dict(ledger) 全拷贝并逐项 led2.get(s, 0.0)，
    # 是 solve_extent 的最大开销（318k 次调用 × O(N) 拷贝）。改为复用单一
    # 工作账本：只更新变化物种，其余保持 ledger 原值——estimate_state /
    # _buffer_titration 内部都自行 dict(ledger) 拷贝，不会污染工作账本。
    changing: list[tuple[str, float, float]] = []  # (species, net_delta_per_x, orig_value)
    _seen = set()
    for s, nu in rr.items():
        if s == WATER or s == H_ION:
            continue
        changing.append((s, -float(nu), ledger.get(s, 0.0)))
        _seen.add(s)
    for s, nu in pp.items():
        if s == WATER or s == H_ION:
            continue
        if s in _seen:
            # 同物种出现在 rr/pp 两侧（理论上 _try_vec 后不会，防御性合并）
            for i, (sp, d, o) in enumerate(changing):
                if sp == s:
                    changing[i] = (sp, d + float(nu), o)
                    break
        else:
            changing.append((s, float(nu), ledger.get(s, 0.0)))
    led_work = dict(ledger)

    def f(x: float) -> float:
        for s, d, orig in changing:
            led_work[s] = orig + d * x
        if c.kind == "redox":
            pH_x, led_v, _ = estimate_state(led_work, H_excess + nu_H * x, V, T, T_K)
            return direction * S_of(c, led_v, V, pH_x, T_K, T, gsup, p_ext_kpa)
        pH_x = estimate_pH(led_work, H_excess + nu_H * x, V, T, T_K)
        return direction * S_of(c, led_work, V, pH_x, T_K, T, gsup, p_ext_kpa)

    f_hi = f(x_max)
    if f_hi > 0:
        return x_max, x_max
    if iters > 20:
        # 主求解（参与路径选择）：纯二分，迭代次数固定。
        # 鞍点/多根体系（NaClO+CO2、Cu+HNO3 的 NO2→NO 脱气伪解通道）中
        # f 非单调、存在多个过零点，求解结果（含数值噪声下的根选择）
        # 直接决定 walk 路径——此处语义被测试套件锁定，不得改动
        # （实测割线/Brent 跨根、迭代压缩均引入回退）。
        lo, hi = 0.0, x_max
        for _ in range(iters):
            mid = (lo + hi) / 2
            if f(mid) > 0:
                lo = mid
            else:
                hi = mid
        return lo, x_max
    # 粗精度求解（iters<=20，仅用于慢标注等布尔阈值判定，不影响路径）：
    # 5 次二分定盆 + Brent 抛光，~12 次求值达到足够精度
    lo, hi = 0.0, x_max
    for _ in range(5):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    xtol = 1e-6 * max(1.0, x_max)
    a, b = lo, hi
    fa, fb = f(a), f(b)
    if fa <= 0:
        return a, x_max
    cc, fcc = b, fb
    d = e = b - a
    for _ in range(iters):
        if (fb > 0) == (fcc > 0):
            cc, fcc = a, fa
            d = e = b - a
        if abs(fcc) < abs(fb):
            a, b, cc = b, cc, b
            fa, fb, fcc = fb, fcc, fb
        tol1 = 2.0 * xtol
        xm = 0.5 * (cc - b)
        if abs(xm) <= tol1 or fb == 0.0:
            break
        if abs(e) >= tol1 and abs(fa) > abs(fb):
            bs = fb / fa
            if a == cc:
                bp = 2.0 * xm * bs
                bq = 1.0 - bs
            else:
                bq = fa / fcc
                br = fb / fcc
                bp = bs * (2.0 * xm * bq * (bq - br) - (b - a) * (br - 1.0))
                bq = (bq - 1.0) * (br - 1.0) * (bs - 1.0)
            if bp > 0:
                bq = -bq
            else:
                bp = -bp
            if 2.0 * bp < min(3.0 * xm * bq - abs(tol1 * bq), abs(e * bq)):
                e, d = d, bp / bq
            else:
                d = e = xm
        else:
            d = e = xm
        a, fa = b, fb
        b = b + d if abs(d) > tol1 else b + (tol1 if xm > 0 else -tol1)
        fb = f(b)
    return b, x_max


def _film_acid_soluble(fp: str, aOH: float, T, T_K: float = 298.15) -> bool:
    """氢氧化物膜的酸可溶性：其阳离子在当前 pH 的饱和活度 >1（可溶 >1 mol/L）
    → 膜无法积累。非氢氧化物膜（PbSO4 等）返回 False（仍按膜封锁判定）。"""
    cell = T.ksp_by_solid.get(fp)
    if cell is None or cell["pair"][1] != "OH^-":
        return False
    n_OH = charge_of(cell["pair"][0])
    pksp = cell["pKsp"] - (_vant(cell["dH"], T_K) if "dH" in cell else 0.0)
    a_sat = 10.0 ** (-pksp) / aOH ** n_OH
    return a_sat > 1.0


# ========================================================== 动力学层：blocked

def blocked_extent(pick: Cand, direction: int, evals: list, T_K: float, T,
                   ledger: dict, H_excess: float, V: float, x_max: float,
                   pH: float = 7.0, p_ext_kpa: float = P_EXT_KPA) -> tuple:
    # 返回 (extent|None, 膜电位 species 字典)；未受阻为 (None, None)
    rr = pick.r if direction > 0 else pick.pr
    pr = pick.pr if direction > 0 else pick.r
    if pick.kind != "redox":
        return None, None
    film_ps = [p for p in pr if T.ksp_by_solid.get(p, {}).get("film")]
    if not film_ps or not any(s in T.solids for s in rr):
        return None, None
    # 酸可溶的氢氧化物膜豁免封锁：膜阳离子在当前 pH 的饱和活度 >1 时
    # 膜生成即溶、无法积累（Zn(OH)2 在 8M HNO3 中——溶解候选只在膜在账时
    # 才枚举（§4.5 需求集），而决策点膜恒被上步清 0，通道检查结构性失效，
    # 故直接以饱和活度判定）。PbSO4/CaSO4 等非氢氧化物膜、中性水的
    # Mg(OH)2/Al(OH)3 膜不受影响
    # 豁免仅在酸性介质成立（pH<6 有酸库持续溶膜）；中性/碱中氢氧化物膜
    # 溶解会使界面 pH 自缓冲升高（Mg+冷水：Mg(OH)2 饱和界面 pH≈10.5，
    # a_sat 跌回 <1），膜依然封锁
    if pH < 6.0:
        aOH = 10.0 ** (pH - pKw_of(T_K))
        film_ps = [p for p in film_ps if not _film_acid_soluble(p, aOH, T, T_K)]
        if not film_ps:
            return None, None
    film_ps = [p for p in film_ps if T_K < T.ksp_by_solid[p].get("unlock_T", 1e9)]
    if not film_ps:
        return None, None
    # 溶解通道能溶走的膜量 ≥ 封锁允许程度 → 膜可穿，不封锁；
    # 否则通道只溶解微量（ACT_FLOOR 播种的假阳性），仍封锁
    allow = BLOCKED_EXTENT * max(x_max, 1e-12)
    for c, d, S in evals:
        reactants = c.r if d > 0 else c.pr
        if S > 0 and any(fp in reactants for fp in film_ps):
            ext_c, _ = solve_extent(c, d, ledger, H_excess, V, T_K, T,
                                    p_ext_kpa=p_ext_kpa)
            if ext_c >= allow:
                return None, None
    return BLOCKED_EXTENT, film_ps


# ========================================================== OVERRIDE（逃生舱）

def _virt_redox_gain(ledger: dict, vled: dict, T) -> bool:
    """滴定后的虚拟账本是否出现了账本上不存在（或已耗尽）的、且能真正配成
    新氧化还原候选的物种。仅"出现且可配对"方向触发惰性实现：
    - "出现"但无配对对象不触发（如 NH3+HCl 中和产生的 NH4+ 虽是 NO3-/NH4+
      电对的还原侧，但账上无 E0>0.88V 的氧化剂，实现滴定只会白白吞掉
      中和步骤的诚实记账）；
    - "消失"方向不触发（消失的形态是酸碱候选自己的记账职责）。
    配对用物种级最高/最低 E0 近似（存在性判定，假阳性无害——枚举层会再过滤）。"""
    for sp, m in vled.items():
        if m <= X_MIN or ledger.get(sp, 0.0) > X_MIN:
            continue
        e_ox = T.redox_ox_E.get(sp)    # 作为氧化剂（被还原）需账上有更弱氧化剂电对的还原剂
        if e_ox is not None:
            for sp2, m2 in vled.items():
                if m2 > X_MIN and sp2 != sp:
                    e_red = T.redox_red_E.get(sp2)
                    if e_red is not None and e_red < e_ox:
                        return True
        e_red = T.redox_red_E.get(sp)  # 作为还原剂（被氧化）需账上有更强氧化剂
        if e_red is not None:
            for sp2, m2 in vled.items():
                if m2 > X_MIN and sp2 != sp:
                    e_o2 = T.redox_ox_E.get(sp2)
                    if e_o2 is not None and e_o2 > e_red:
                        return True
    return False


def _match_override(substances: list[dict], V: float, T_K: float, T):
    names = {i["name"] for i in substances}
    # 按匹配物种数降序：最具体的规则优先（如 KO2+CO2 先于 KO2+水）
    for o in sorted(T.overrides, key=lambda x: -len(x["match"].get("species", []))):
        m = o["match"]
        if not set(m.get("species", [])) <= names:
            continue
        if m.get("conc") == "concentrated":
            if not any(i["name"] in T.conc and i["mol"] / V >= T.conc[i["name"]] for i in substances):
                continue
        if "T_min" in m and T_K < m["T_min"]:
            continue
        if "T_max" in m and T_K > m["T_max"]:
            continue
        return o
    return None


# ========================================================== 主循环

_COND_KEYS = {"V_L", "T_K", "T_C", "c_H", "c_OH", "pH", "p_kpa"}


def judge(substances: list[dict], conditions: dict | None, T: Tables) -> dict:
    conditions = dict(conditions or {})
    bad = set(conditions) - _COND_KEYS
    if bad:
        # 条件键静默忽略是定义错位的温床（如 T_C 被当 298K 跑），宁可报错
        raise ValueError(f"未知条件键 {sorted(bad)}；支持 {sorted(_COND_KEYS)}")
    if "T_C" in conditions and "T_K" not in conditions:
        conditions["T_K"] = conditions["T_C"] + 273.15
    cond = {"V_L": 1.0, "T_K": 298.15, "c_H": None, "c_OH": None, "pH": None,
            "p_kpa": P_EXT_KPA}
    cond.update(conditions)
    V, T_K = cond["V_L"], cond["T_K"]
    p_ext_kpa = float(cond["p_kpa"])
    if not 273.15 <= T_K <= 373.15:
        # 常压液态水温度域：域外水的存在形式/活度约定全部失效（van't Hoff
        # ΔCp≈0 近似与 Hill 形态曲线也仅在此域内标定），宁可报错不静默
        raise ValueError(f"T_K={T_K} 超出液态水温度域 273.15–373.15 K")
    if p_ext_kpa <= 0.0:
        raise ValueError(f"p_kpa={p_ext_kpa} 必须为正（kPa）")

    ov = _match_override(substances, V, T_K, T)
    if ov is not None:
        res = dict(ov["result"])
        # 按限量试剂缩放化学计量（result 中的 mol 为每单位反应式的量）
        led0, _He0, _st0, _un0 = normalize(substances, cond, T)
        scale = float("inf")
        for c0 in res.get("consumed", []):
            # H2O 为溶剂不记账；H+/OH- 以 He 记账——按游离强酸/强碱储备限量
            # （原一律跳过限量，酸/碱不足时 override 会过量产出）
            if c0["name"] == WATER:
                continue
            if c0["name"] == H_ION:
                scale = min(scale, max(_He0, 0.0) / c0["mol"])
                continue
            if c0["name"] == "OH^-":
                scale = min(scale, max(-_He0, 0.0) / c0["mol"])
                continue
            scale = min(scale, led0.get(c0["name"], 0.0) / c0["mol"])
        if scale == float("inf"):
            scale = 1.0
        scale = max(scale, 0.0)
        res["consumed"] = [dict(c0, mol=round(c0["mol"] * scale, 6))
                           for c0 in res.get("consumed", [])]
        res["produced"] = [dict(c0, mol=round(c0["mol"] * scale, 6))
                           for c0 in res.get("produced", [])]
        res["steps"] = []
        res["final"] = []
        res["unknown"] = []
        res["override"] = ov["id"]
        res["final_pH"] = None
        return res

    ledger, H_excess, steps, unknown = normalize(substances, cond, T)
    initial = dict(ledger)
    # 初始投料中的气体 = 持续供给（按账本活度）；反应自产气体按 A_GAS 逸出
    gsup = frozenset(sp for sp in initial if sp in T.gases and initial[sp] > X_MIN)
    annotations: list[str] = []
    blocked_solids: dict[str, list] = {}   # 被膜封锁的金属 -> 膜固相列表
    disabled: dict = {}   # (key, d) -> 禁用时的账本签名；状态实质漂移后自动解禁
    slow_seen = False

    def _sig():
        # 账本签名：用于 disabled 过期判定。纯震荡会回到原签名（禁用保持），
        # 状态漂移（其他通道推进）则签名改变 → 解禁，避免误杀平衡已移动的通道
        return (tuple(sorted((s, round(m, 5)) for s, m in ledger.items()
                             if s != WATER and m > X_MIN)), round(H_excess, 5))

    def _refresh_disabled():
        sig = _sig()
        for k, sig0 in list(disabled.items()):
            if sig != sig0:
                del disabled[k]
        for k, sig0 in list(frozen_net.items()):
            if sig != sig0:
                del frozen_net[k]
    # 净反应平衡冻结：同一净反应（忽略 H2O/H+）一旦正向执行过，其逆反应
    # 再出现时视为已达平衡，永久跳过——阻断"正反向净零震荡"（浓酸体系中
    # 同一反应因 H+ 挂侧不同生成多个配平形式，互相逆转耗光迭代而净零）
    hist: list = []   # 已执行净反应键序列（用于循环震荡检测）
    frozen_net: dict = {}   # 已宣告平衡的净反应键（含镜像）-> 冻结时签名；
    # 状态实质漂移后解冻（逆反应出现只说明"此刻"平衡，后续通道推进会移动平衡）
    frozen_perm: set = set()   # 实测/极限环/短周期震荡判定后永久冻结的净反应键

    def _netkey(c, direction):
        rr = c.r if direction > 0 else c.pr
        pp = c.pr if direction > 0 else c.r
        r2 = tuple(sorted((s, n) for s, n in rr.items() if s not in (WATER, H_ION)))
        p2 = tuple(sorted((s, n) for s, n in pp.items() if s not in (WATER, H_ION)))
        return r2, p2

    seen_sig: dict = {}
    idle = 0   # 连续零执行迭代计数：签名不变 ⇒ disabled/frozen 永不刷新，
               # 空转只会无限重复同一评估，达阈值即宣告收敛
    for it in range(MAX_ITER):
        _refresh_disabled()
        H_excess = _respeciate_strong_acids(ledger, H_excess, V, T)
        if it == 0:
            # 介质类别快照：浓/稀判定按投料初始的分子态酸浓度做一次后冻结
            gate_ctx = {acid: ledger.get(acid, 0.0) / V
                        for acid in _RESPECIATE_ACIDS}
        pH = estimate_pH(ledger, H_excess, V, T, T_K)
        vled, He_v = _full_speciation(ledger, H_excess, V, T, T_K)
        if vled is not ledger and _virt_redox_gain(ledger, vled, T):
            # 惰性实现酸碱平衡：质子转移远快于氧化还原，强酸/强碱下的自由形态
            # （如 VO3-→VO2+）应直接参与氧化还原竞争——否则 H+/Zn 会在 V(V)
            # 未现身时抢跑耗尽 Zn。仅在"多级形态分布出现了账本上不存在的、
            # 且能配成新电对的氧化还原物种"时才落实为真实账本：全局落实会让
            # 酸碱候选失去诚实的 He 累积（Al3+ 水解酸性被吸回、NH4Ac 解离
            # 幻影循环、半中和 Henderson 失效），故保持惰性。pH 重算保持一致。
            if _TRACE: print('  [realize]', [sp for sp, m in vled.items()
                              if m > X_MIN and ledger.get(sp, 0.0) <= X_MIN])
            ledger, H_excess = vled, He_v
            pH = estimate_pH(ledger, H_excess, V, T, T_K)
        cands = enumerate_candidates(ledger, H_excess, pH, V, T_K, T, gate_ctx)
        evals = []
        slow_now = False
        for c in cands:
            if (c.key, 1) in disabled and (c.key, -1) in disabled:
                continue
            if c.kind == "redox" and any(s in blocked_solids for s in list(c.r) + list(c.pr)
                                         if s not in (WATER, H_ION)):
                continue
            pres_r = all(ledger.get(s, 0.0) > X_MIN for s in c.r if s not in (WATER, H_ION))
            pres_p = all(ledger.get(s, 0.0) > X_MIN for s in c.pr if s not in (WATER, H_ION))
            if not pres_r and not pres_p:
                continue
            S_fwd = S_of(c, ledger, V, pH, T_K, T, gsup, p_ext_kpa)
            if pres_r and S_fwd > 0 and (c.key, 1) not in disabled:
                d, S = 1, S_fwd
            elif pres_p and S_fwd < 0 and (c.key, -1) not in disabled:
                d, S = -1, -S_fwd
            else:
                continue
            if c.meta.get("slow") or d in c.meta.get("slow_dirs", ()):
                # 仅当慢反应可达显著程度才标注（忽略 ACT_FLOOR 量级通道）
                # 慢标注只需判定"能否达 1e-3 量级"：粗精度 12 次二分足够（分辨率 ~2.4e-4，对 1e-3 阈值充分）
                # （60 次高精度为鞍点路径敏感场景保留，此处无需）；
                # 标注是布尔终态——一旦确认过，后续迭代不再重复求解
                if not slow_seen:
                    ext_s, _ = solve_extent(c, d, ledger, H_excess, V, T_K, T, gsup,
                                            iters=12, p_ext_kpa=p_ext_kpa)
                    if ext_s >= ANN_MIN_EXTENT:
                        slow_now = True
                continue
            # 平衡冻结在候选评估阶段排除（而非 pick 后跳过）：
            # 已宣告平衡的净反应不参与竞争，让次优通道（如 ksp_beta）接手
            nk_c = _netkey(c, d)
            if nk_c in frozen_perm:
                if _TRACE: print('  [frozen]', c.key, d)
                continue
            evals.append((c, d, S))
        slow_seen = slow_seen or slow_now
        if not evals:
            break

        # blocked 再验证（每轮）：膜已消失，或溶解通道能力 ≥ 现存膜量 → 解除封锁。
        # 封锁只在"通道溶不动膜"时维持（如 Mg/冷水、Zn/纯水）
        for m, films in list(blocked_solids.items()):
            alive = [fp for fp in films if ledger.get(fp, 0.0) > X_MIN]
            if not alive:
                del blocked_solids[m]
                continue
            for c2, d2, S2 in evals:
                reactants2 = c2.r if d2 > 0 else c2.pr
                targets = [fp for fp in alive if fp in reactants2]
                if S2 > 0 and targets:
                    ext_c, _ = solve_extent(c2, d2, ledger, H_excess, V, T_K, T, gsup,
                                            p_ext_kpa=p_ext_kpa)
                    if ext_c >= min(ledger.get(fp, 0.0) for fp in targets):
                        del blocked_solids[m]
                        break

        # 溶剂优先（界面动力学规则，仅此一条）：无游离强酸且**无更强氧化剂在账**
        # 时水还原优先（防止裸离子路径虚报；强氧化剂在场时按 S 正常竞争）
        strong_ox = [e for e in evals if e[0].kind == "redox"
                     and e[0].meta.get("ox_couple") != H_ION and e[2] >= SOLVENT_FIRST]
        water_c = [] if strong_ox else [
            e for e in evals if e[0].meta.get("ox_couple") == H_ION
            and e[2] >= SOLVENT_FIRST and H_excess / V < 1e-3]
        pick, d, S = max(water_c, key=lambda e: e[2]) if water_c else max(evals, key=lambda e: e[2])
        if _TRACE: print('  [pick]', pick.key, d, round(S,2))

        nk = _netkey(pick, d)

        ext, x_max = solve_extent(pick, d, ledger, H_excess, V, T_K, T, gsup,
                                  p_ext_kpa=p_ext_kpa)
        if _TRACE: print('    [ext]', round(ext,5), 'pH', round(pH,2), 'He', round(H_excess,4))
        bext, film_ps = blocked_extent(pick, d, evals, T_K, T, ledger, H_excess, V, x_max, pH,
                                       p_ext_kpa=p_ext_kpa)
        if bext is not None and ext > bext:
            ext = bext
            if "blocked" not in annotations:
                annotations.append("blocked")
            rr0 = pick.r if d > 0 else pick.pr
            for s in rr0:
                if s in T.solids:
                    blocked_solids[s] = film_ps
        # 振荡外推加速：短周期（2/3 通道）等比衰减爬行（沉淀↔逆转化乒乓、
        # 溶解↔双沉淀三循环等，步长比 ρ→1）时，几何级数剩余工作量
        # ≈ ext·ρ/(1−ρ)，一次补齐直抵不动点附近，避免数百步微步爬行；
        # 补齐受 x_max 钳制，外推失准由 S<0 反向步自动纠回
        # （不动点两侧 S 变号是引擎的既有自校正）
        for _p in range(2, 7):
            if len(hist) < 2 * _p or ext <= 0:
                continue
            tail = hist[-2 * _p:]
            keys = [k for k, _ in tail]
            if keys[:_p] != keys[_p:] or keys[0] != nk:
                continue
            if len({k for k in keys}) < 2:
                continue
            e0, e1_ = tail[0][1], tail[_p][1]
            if e0 <= 0 or e1_ <= 0:
                continue
            rho = e1_ / e0
            if 0.5 < rho < 0.999:
                jump = min(x_max, ext / (1 - rho)) - ext
                if jump > 10 * ext:
                    if _TRACE: print('    [osc]', _p, round(rho, 3), '+', round(jump, 4))
                    ext += jump
            break
        if ext <= max(X_MIN, 1e-4 * x_max):
            # 微步（已达平衡或程度可忽略）：双向禁用该平衡，等待状态实质改变。
            # 程度绝对可辨（>X_MIN）时先执行这一次再禁用——微溶盐（AgCl、
            # BaSO4 等）的溶解度就是这一步到位的热力学终态，直接跳过会把
            # 微溶事实整体漏报；真正的零推进由签名机制在下一轮挡下
            sig0 = _sig()
            disabled[(pick.key, d)] = sig0
            disabled[(pick.key, -d)] = sig0
            # 仅溶解/沉淀类微步执行（微溶盐终态）；质子/氧化还原微步仍跳过——
            # 后者执行会经签名变化逐对渗漏（NH4Ac 双水解曾被渗到 pH 9.4）
            if ext <= X_MIN or pick.kind not in ("dissolve", "precip"):
                idle += 1
                if idle >= 8:
                    break
                continue
            stall = True       # 执行但不解禁（等价 stall 语义）
        else:
            stall = ext < STALL_FRAC * x_max
            if not stall:
                disabled.clear()   # 状态将发生实质改变，解禁全部（签名机制双保险）

        idle = 0
        rr = pick.r if d > 0 else pick.pr
        pp = pick.pr if d > 0 else pick.r
        for s, nu in rr.items():
            if s == H_ION:
                H_excess -= nu * ext
            elif s != WATER:
                ledger[s] = ledger.get(s, 0.0) - nu * ext
        for s, nu in pp.items():
            if s == H_ION:
                H_excess += nu * ext
            elif s != WATER:
                ledger[s] = ledger.get(s, 0.0) + nu * ext
        eq = " + ".join(f"{_fmt(nu)}{s}" for s, nu in rr.items() if s != WATER)
        eq += " -> " + " + ".join(f"{_fmt(nu)}{s}" for s, nu in pp.items() if s != WATER)
        lims = [initial.get(s, 0.0) / nu for s, nu in rr.items()
                if s not in (WATER, H_ION) and initial.get(s, 0.0) > 0]
        lim0 = min(lims) if lims else 0.0
        abs_conv = min(1.0, ext / lim0) if lim0 > 0 else (1.0 if ext > 0 else 0.0)
        steps.append({"kind": pick.kind, "equation": eq, "logK": round(logK_T(pick, T_K), 2),
                      "S": round(S, 2), "extent": round(ext, 6),
                      "conversion": round(min(1.0, ext / x_max), 4) if x_max > 0 else 1.0,
                      "abs_conv": round(abs_conv, 4)})
        hist.append((nk, ext))
        # 极限环检测（仅在实质步后判定）：账本签名精确复现 ⇒ 确定性求解器
        # 进入零净推进循环，永久冻结窗口内全部净反应让其他通道接手；
        # 窗口为空（微步原地）则不动作，交由 disabled/微步机制处理
        sig_now = _sig()
        if sig_now in seen_sig:
            window = {k for k, _ in hist[seen_sig[sig_now]:]}
            window -= {k for k in window if k in frozen_perm}
            if window:
                for k in window:
                    frozen_perm.add(k)
                    frozen_perm.add((k[1], k[0]))
                if _TRACE: print('  [freeze-limit-cycle]', window)
        else:
            seen_sig[sig_now] = len(hist)
        # 实测震荡判定（近窗）：最近 10 步内正反向执行程度接近抵消
        # （|净| < 10% 总量）且总量显著 → 原地空转，永久冻结。
        # 限近窗是因为跨长程的"抵消"往往是平衡被其他通道移动后的正常演化
        rev = (nk[1], nk[0])
        recent = hist[-10:]
        ext_fwd = sum(e for k, e in recent if k == nk)
        ext_rev = sum(e for k, e in recent if k == rev)
        gross = ext_fwd + ext_rev
        if gross >= 0.05 and abs(ext_fwd - ext_rev) <= 0.1 * gross:
            frozen_perm.add(nk)
            frozen_perm.add(rev)
            if _TRACE: print('  [freeze-perm]', nk, 'gross', round(gross, 3))
        # 循环震荡检测：最近若干步为同一短周期（长度 2 或 3）反复且总推进量
        # 低于显著阈值 → 冻结该周期涉及的全部净反应（E35 类阶梯每周期有实质
        # 推进，不受影响；34 类 A->B->A->B 原地空转被捕获）
        for period in (2, 3):
            w = 3 * period
            if len(hist) >= w:
                tail = [k for k, _ in hist[-w:]]
                unit = tail[:period]
                recent_ext = [e for _, e in hist[-w:]]
                if tail == unit * 3 and sum(recent_ext) < 1e-3:
                    for k in set(unit):
                        frozen_perm.add(k)
                        frozen_perm.add((k[1], k[0]))
                    if _TRACE: print('  [freeze-cycle]', unit)
        if stall:
            # 停滞步：该方向疑似与其他候选刚性互锁（各自小步震荡），禁用本方向
            # 让次优候选（如 ksp_beta 换算通道）接手；实质步发生时才解禁
            disabled[(pick.key, d)] = _sig()

    if slow_seen:
        annotations.append("slow")

    consumed = [{"name": s, "mol": round(initial[s] - ledger.get(s, 0.0), 6)}
                for s in initial if s != WATER and not s.startswith("__")
                and initial[s] - ledger.get(s, 0.0) > 1e-6]
    produced = [{"name": s, "mol": round(ledger.get(s, 0.0) - initial.get(s, 0.0), 6)}
                for s in ledger if s != WATER and not s.startswith("__")
                and ledger.get(s, 0.0) - initial.get(s, 0.0) > 1e-6]
    final = [{"name": s, "mol": round(m, 6)} for s, m in ledger.items()
             if s != WATER and not s.startswith("__") and m > 1e-6]
    main_steps = [st for st in steps if st.get("extent", 0) >= ANN_MIN_EXTENT
                  and st["kind"] != "neutralize"]
    reacted = (any(e["mol"] >= ANN_MIN_EXTENT for e in consumed)
               or any(st["kind"] == "neutralize" for st in steps))
    if not main_steps:
        conv0 = max((st.get("abs_conv", st.get("conversion", 0.0)) for st in steps),
                    default=0.0)
        if conv0 >= DEGREE_COMPLETE:
            degree = "complete"
        elif conv0 >= DEGREE_PARTIAL:
            degree = "incomplete"
        else:
            degree = "hardly" if (annotations or steps) else "none"
    else:
        lead = max(main_steps, key=lambda st: st["extent"])   # 主步：程度最大者
        conv = lead.get("abs_conv", lead["conversion"])
        # 多通道补全：主步转化率略低于阈值，但某初始反应物跨通道总体耗尽
        # （≥DEGREE_COMPLETE）时仍判 complete（反应已被驱动到底）
        if conv < DEGREE_COMPLETE and conv >= DEGREE_PARTIAL:
            exhausted = any(
                initial.get(s, 0.0) > ANN_MIN_EXTENT
                and (initial[s] - ledger.get(s, 0.0)) / initial[s] >= DEGREE_COMPLETE
                for s in initial if s != WATER)
            if exhausted:
                conv = 1.0
        degree = "complete" if conv >= DEGREE_COMPLETE else (
            "incomplete" if conv >= DEGREE_PARTIAL else "hardly")
    return {
        "reacted": reacted, "degree": degree, "annotations": annotations,
        "consumed": consumed, "produced": produced, "final": final,
        "steps": steps, "unknown": unknown,
        "final_pH": round(estimate_pH(ledger, H_excess, V, T, T_K), 2),
        "H_excess": round(H_excess, 6),
        "override": None,
    }


def _fmt(nu: float) -> str:
    return "" if nu == 1 else f"{nu:g}"
