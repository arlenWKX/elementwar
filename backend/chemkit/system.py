"""chemkit.system：反应体系与反应结果对象（高层 API）。

设计原则：
  - 数据表在 `import chemkit` 时即加载（缓存于 data._TABLES_CACHE，全程序仅一次）。
  - System 参数采用直观命名（V/T/p），默认值贴近常温常压水溶液。
  - Result 字段分为 raw 与非 raw：
      raw  = 引擎原本记账方式（H_excess 账本，H2O/H+/OH- 不在 consumed/produced）
      非 raw = 化学习惯（H+/OH-/H2O 显式出现，离子方程式配平到最简整数比）
  - 方程式统一为单一 `equation` 属性（净离子方程式），不再区分 equations / net_equation。

用法：
    import chemkit
    sys = chemkit.System(V=1.0, T=298.15, p=101.3)
    r = sys.add("NaOH", 0.1).add("HCl", 0.1)   # 累计投料
    r.consumed, r.produced, r.equation          # 离子层面（含 H+/OH-/H2O）
    r.consumed_raw, r.produced_raw              # 引擎原始记账

    # 一步式：
    r = chemkit.react({"Zn": 1.0, "H_2SO_4": 1.0}, V=1.0)
    print(r.equation)   # 'Zn + 2H^+ -> H_2 + Zn^{2+}'
"""
from __future__ import annotations

import re
from fractions import Fraction
from math import gcd
from functools import reduce

from .data import Tables, load_tables
from .engine import judge
from .core import elements_of

# ---------- 数据表预加载（import chemkit 时执行，依赖 data.py 缓存）----------
TABLES: Tables = load_tables()


def default_tables() -> Tables:
    """返回模块级默认数据表（兼容旧 API，直接返回预加载的 TABLES 单例）。"""
    return TABLES


# ============================================================ 方程式解析
# 引擎 step.equation 形如 '2NO_3^- + 3Cu + 8H^+ -> 2NO + 3Cu^{2+}'
# neutralize 步骤用旧式 'H+ + OH- -> H2O'，需归一化到引擎标准记法。

_SPECIES_NORMALIZE = {
    "H2O": "H_2O",
    "H+": "H^+",
    "OH-": "OH^-",
}

# 匹配 "系数+物种"，系数为可选的整数或小数；物种以非数字开头（字母/[/()
_TERM_RE = re.compile(r"^(\d+(?:\.\d+)?)(\D.*)$")
_SIDE_SEP = " + "
_ARROW = " -> "


def _parse_side(s: str) -> dict[str, float]:
    """解析 '2A + 3B' → {A: 2.0, B: 3.0}。

    以 ' + '（空格+加号+空格）为分隔符，避免与电荷符号 '^+' / '^{2+}' 冲突。
    """
    result: dict[str, float] = {}
    for term in s.split(_SIDE_SEP):
        term = term.strip()
        if not term:
            continue
        m = _TERM_RE.match(term)
        if m:
            coef = float(m.group(1))
            species = m.group(2).strip()
        else:
            coef = 1.0
            species = term
        species = _SPECIES_NORMALIZE.get(species, species)
        result[species] = result.get(species, 0.0) + coef
    return result


def _parse_equation(eq: str) -> tuple[dict[str, float], dict[str, float]]:
    """解析 '2A + 3B -> 4C + D' → ({A:2, B:3}, {C:4, D:1})。"""
    if _ARROW not in eq:
        return {}, {}
    lhs, rhs = eq.split(_ARROW, 1)
    return _parse_side(lhs), _parse_side(rhs)


# ============================================================ H/O 原子配平
# 引擎 step.equation 过滤了 H2O（溶剂），但离子方程式需要显式 H2O。
# 对净反应的 H、O 原子差，用 H2O（必要时加 H+）配平。

WATER = "H_2O"
H_ION = "H^+"


def _balance_h_o(consumed: dict[str, float], produced: dict[str, float]
                 ) -> tuple[dict[str, float], dict[str, float]]:
    """用 H2O（必要时 H+）配平 H、O 原子。

    引擎方程已用 H+ 正则化（OH- → H2O/H+），所以 H/O 不平衡仅由 H2O 被过滤导致。
    正常情况 h_diff = 2 × o_diff，纯加 H2O 即可；异常时退化为 H2O + H+。
    """
    h_lhs = sum(nu * elements_of(sp).get("H", 0) for sp, nu in consumed.items())
    h_rhs = sum(nu * elements_of(sp).get("H", 0) for sp, nu in produced.items())
    o_lhs = sum(nu * elements_of(sp).get("O", 0) for sp, nu in consumed.items())
    o_rhs = sum(nu * elements_of(sp).get("O", 0) for sp, nu in produced.items())

    o_diff = o_lhs - o_rhs      # 正：产物侧缺 O → 加 H2O 到产物
    h_diff = h_lhs - h_rhs      # 正：产物侧缺 H

    if abs(h_diff - 2 * o_diff) < 1e-6:
        # 纯 H2O 配平
        if o_diff > 1e-9:
            produced[WATER] = produced.get(WATER, 0.0) + o_diff
        elif o_diff < -1e-9:
            consumed[WATER] = consumed.get(WATER, 0.0) + (-o_diff)
    else:
        # 先用 H2O 配 O，再用 H+ 配 H（理论上不会触发，防御性兜底）
        if o_diff > 1e-9:
            produced[WATER] = produced.get(WATER, 0.0) + o_diff
            h_rhs += 2 * o_diff
        elif o_diff < -1e-9:
            consumed[WATER] = consumed.get(WATER, 0.0) + (-o_diff)
            h_lhs += 2 * (-o_diff)
        h_diff = h_lhs - h_rhs
        if h_diff > 1e-9:
            produced[H_ION] = produced.get(H_ION, 0.0) + h_diff
        elif h_diff < -1e-9:
            consumed[H_ION] = consumed.get(H_ION, 0.0) + (-h_diff)
    return consumed, produced


# ============================================================ 系数有理化
def _fmt_term(nu: float, species: str) -> str:
    """格式化方程式一项：系数 1 省略，整数显示整数，浮点保留 3 位。"""
    # 容差：接近整数则取整
    if abs(nu - round(nu)) < 1e-6:
        nu = int(round(nu))
    if nu == 1:
        return species
    if isinstance(nu, int) or (isinstance(nu, float) and nu == int(nu)):
        return f"{int(nu)}{species}"
    return f"{nu:.3g}{species}"


def _rationalize(vals: list[float]) -> list[int] | None:
    """浮点系数列表 → 最简整数比列表（用 Fraction 精确有理化）。

    先按最小值归一化（使最小系数=1），再对归一化值做容差圆整：
      - 接近整数（±5%）→ 圆到整数
      - 接近半整数（n+0.5，±5%）→ 圆到 n+0.5
    消除 3.01→3、1.498→1.5 之类的数值噪声。最后用 Fraction 精确有理化。
    """
    pos_vals = [v for v in vals if v > 0]
    if not pos_vals:
        return None
    min_v = min(pos_vals)
    norm_raw = [v / min_v for v in vals]
    rounded = []
    for v in norm_raw:
        if v <= 0:
            rounded.append(v)
            continue
        r = round(v)
        if r > 0 and abs(v - r) / v < 0.05:
            rounded.append(float(r))
            continue
        # 检查半整数（n + 0.5）
        h = round(v * 2) / 2.0
        if h > 0 and abs(v - h) / v < 0.05:
            rounded.append(h)
            continue
        rounded.append(v)
    fracs = [Fraction(v).limit_denominator(10000) for v in rounded]
    pos = [f for f in fracs if f > 0]
    if not pos:
        return None
    min_frac = min(pos)
    norm = [f / min_frac for f in fracs]
    denoms = [f.denominator for f in norm]
    common_denom = reduce(lambda a, b: a * b // gcd(a, b), denoms, 1)
    int_vals = [int(f * common_denom) for f in norm]
    g = reduce(gcd, [v for v in int_vals if v > 0], 0)
    if g == 0:
        return None
    return [v // g for v in int_vals]


def _format_equation(consumed: dict[str, float], produced: dict[str, float]) -> str | None:
    """将 consumed/produced 格式化为最简整数比的离子方程式字符串。"""
    if not consumed or not produced:
        return None
    all_vals = list(consumed.values()) + list(produced.values())
    int_vals = _rationalize(all_vals)
    if int_vals is None:
        return None
    if max(int_vals) > 1000:
        # 比例不整除，退化为浮点系数（按最小值归一化）
        scale = min(all_vals)
        cons_items = sorted(((sp, v / scale) for sp, v in consumed.items()),
                            key=lambda x: (-x[1], x[0]))
        prod_items = sorted(((sp, v / scale) for sp, v in produced.items()),
                            key=lambda x: (-x[1], x[0]))
    else:
        n_cons = len(consumed)
        cons_ints = int_vals[:n_cons]
        prod_ints = int_vals[n_cons:]
        cons_items = sorted(zip(consumed.keys(), cons_ints),
                            key=lambda x: (-x[1], x[0]))
        prod_items = sorted(zip(produced.keys(), prod_ints),
                            key=lambda x: (-x[1], x[0]))
    lhs = _SIDE_SEP.join(_fmt_term(v, sp) for sp, v in cons_items)
    rhs = _SIDE_SEP.join(_fmt_term(v, sp) for sp, v in prod_items)
    return f"{lhs}{_ARROW}{rhs}"


# ============================================================ 净离子方程式构建

# 净差阈值（mol）：低于此值的物种视为数值噪声，不进入方程。
# 设为 0.01 以过滤近抵消的中间体（如 Cu+稀HNO3 中 NO2 先产再耗，
# 残余 ~0.01 mol 不应出现在净方程中）。
_TRACE = 0.01


def _build_ionic(steps: list[dict]) -> tuple[dict[str, float], dict[str, float], str | None]:
    """从逐步反应过程构建净离子消耗/生成量与方程式。

    算法：
      1. 解析每个显著步骤（extent ≥ 1e-3）的 equation 字符串
      2. 按 extent 加权汇总所有物种的净变化（产物侧 +，反应物侧 −）
      3. 中间体（如 HCO3⁻ 在 CaCO3+HCl 中先产再耗）会自然抵消
      4. 用 H2O 配平 H/O 原子（引擎方程过滤了 H2O）
      5. H2O/H+ 正则形还原为 OH⁻（引擎把 OH⁻ 记为 H2O−H+，
         非 raw 还原：反应物 H2O + 产物 H+ → 反应物 OH⁻）
      6. 过滤痕量物种（< 1e-4 mol），有理化系数到最简整数比
      7. 旁观离子过滤：dissolve 步骤产生的离子若不再参与其他步骤，
         视为旁观（如 NaHCO3→Na⁺+HCO3⁻ 中 Na⁺ 不参与后续 HCO3⁻+H⁺→CO2）

    返回 (consumed, produced, equation)。
    """
    # 步骤显著性阈值：extent < 1e-3 视为数值噪声不参与方程。
    # 但对"主反应占总 extent ≥90%"的体系，过滤更严格——微量副反应
    # （如 AgCl+氨水中的 NH3 质子化、Na2CO3+CaCl2 中的微量 CO3 水解）
    # 会引入噪声物种（H⁺/NH4⁺/HCO3⁻）污染方程。
    STEP_MIN = 1e-3

    # 第一遍：收集所有显著步骤，保留 kind 信息
    # dissolve 步骤（NaHCO3→Na⁺+HCO3⁻）是物理溶解而非化学反应，
    # 离子方程式中跳过——真正参与反应的是溶解后的离子，由后续步骤体现。
    significant: list[tuple[str, dict, dict, float]] = []  # (kind, reactants, products, extent)
    for st in steps:
        ext = st.get("extent", 0.0)
        if ext < STEP_MIN:
            continue
        kind = st.get("kind", "")
        if kind == "dissolve":
            continue   # 物理溶解，离子方程式不体现
        eq = st.get("equation", "")
        if not eq:
            continue
        reactants, products = _parse_equation(eq)
        if not reactants and not products:
            continue
        significant.append((kind, reactants, products, ext))

    if not significant:
        return {}, {}, None

    # 找主反应步骤（extent 最大的非 neutralize 步骤）
    # 微量步骤（< 主反应 extent 的 5%）视为噪声过滤
    main_ext = max((ext for _, _, _, ext in significant if ext > 0), default=0.0)
    NOISE_FRAC = 0.05  # < 主反应 5% 的步骤视为噪声
    significant = [s for s in significant if s[3] >= main_ext * NOISE_FRAC]

    if not significant:
        return {}, {}, None

    # 旁观离子识别：只在单个步骤中出现且该步骤是溶解/拆分类的离子
    # （dissolve 已跳过，但 normalize 阶段拆解的离子如 Na⁺ 可能不出现在
    # 任何步骤中——这里主要处理 derived 类步骤中的旁观离子）
    species_step_count: dict[str, int] = {}
    for _, r, p, _ in significant:
        for sp in list(r.keys()) + list(p.keys()):
            species_step_count[sp] = species_step_count.get(sp, 0) + 1

    spectator_ions: set[str] = set()
    # 启发式：离子（charge_of != 0）且只在单个步骤中出现，
    # 且该步骤有其他更主要的离子参与 → 可能是旁观
    # （保守起见，只过滤明确只出现一次且不在主反应步骤中的离子）
    # 暂不激进过滤，避免误删

    # 汇总净变化（排除旁观离子）
    net: dict[str, float] = {}
    for _, reactants, products, ext in significant:
        for sp, coef in reactants.items():
            if sp in spectator_ions:
                continue
            net[sp] = net.get(sp, 0.0) - coef * ext
        for sp, coef in products.items():
            if sp in spectator_ions:
                continue
            net[sp] = net.get(sp, 0.0) + coef * ext

    consumed = {sp: -v for sp, v in net.items() if v < -_TRACE}
    produced = {sp: v for sp, v in net.items() if v > _TRACE}
    if not consumed or not produced:
        return {}, {}, None

    # 配平 H/O 原子（补 H2O，必要时补 H+）
    consumed, produced = _balance_h_o(consumed, produced)

    # H2O/H+ 正则形还原为 OH⁻：
    # 引擎把 OH⁻ 记为 H2O（反应物）+ H+（产物），即 M + nOH⁻ → M(OH)n 被记为
    # M + nH2O → M(OH)n + nH+。非 raw 还原：若 H2O 在反应物、H+ 在产物，
    # 且量匹配（H2O 系数 = H+ 系数），合并为 OH⁻ 在反应物侧。
    # 注意：只在"H2O 反应物 + H+ 产物"方向还原——这是 OH⁻ 被正则化的标志。
    # 反向（H2O 产物 + H+ 反应物）不还原：H2O 是真实产物（如 Cu+HNO3 产生 H2O）。
    w_consumed = consumed.get(WATER, 0.0)
    h_produced = produced.get(H_ION, 0.0)
    if w_consumed > _TRACE and h_produced > _TRACE:
        merge = min(w_consumed, h_produced)
        consumed[WATER] = w_consumed - merge
        produced[H_ION] = h_produced - merge
        oh = "OH^-"
        consumed[oh] = consumed.get(oh, 0.0) + merge
        if consumed[WATER] <= _TRACE:
            consumed.pop(WATER, None)
        if produced[H_ION] <= _TRACE:
            produced.pop(H_ION, None)

    # 重新过滤（配平/还原可能引入微小量）
    consumed = {sp: v for sp, v in consumed.items() if v > _TRACE}
    produced = {sp: v for sp, v in produced.items() if v > _TRACE}
    if not consumed or not produced:
        return {}, {}, None

    equation = _format_equation(consumed, produced)
    return consumed, produced, equation


# ============================================================ Result

class Result:
    """一次反应的结果。

    公共属性（非 raw，化学习惯）：
        reacted       是否发生反应
        degree        程度（complete / incomplete / hardly / none）
        consumed      净离子消耗 {化学式: mol}（含 H⁺/OH⁻/H₂O）
        produced      净离子生成 {化学式: mol}（含 H⁺/OH⁻/H₂O）
        final         终态离子组成 {化学式: mol}（含 H⁺/OH⁻，H₂O 为溶剂不入）
        pH            终态 pH
        annotations   标注列表（slow / blocked / gas 等）
        override      命中的 OVERRIDE id（或 None）

    公共属性（raw，引擎记账）：
        consumed_raw  引擎原始消耗 {化学式: mol}（H⁺/OH⁻/H₂O 不在内）
        produced_raw  引擎原始生成 {化学式: mol}
        final_raw     引擎原始终态 {化学式: mol}
        H_excess_raw  终态 H⁺ 账本（正=残余游离酸，负=残余游离碱）

    公共属性（property）：
        equation      净离子方程式字符串（如 'Zn + 2H^+ -> H_2 + Zn^{2+}'），无反应时为 None
        steps         逐步反应过程（来自 raw['steps']）

    原始数据：
        raw           judge() 原始 dict（备用，不鼓励直接读取）
    """
    __slots__ = (
        # 非 raw
        "consumed", "produced", "final",
        # raw
        "consumed_raw", "produced_raw", "final_raw", "H_excess_raw",
        # 通用
        "reacted", "degree", "pH", "annotations", "override", "raw",
        # 缓存
        "_equation",
    )

    def __init__(self, r: dict):
        self.raw = r
        self.reacted: bool = r["reacted"]
        self.degree: str = r["degree"]
        self.pH: float | None = r["final_pH"]
        self.annotations: list[str] = list(r["annotations"])
        self.override: str | None = r.get("override")

        # ---- raw（引擎记账）----
        self.consumed_raw: dict[str, float] = {e["name"]: e["mol"] for e in r["consumed"]}
        self.produced_raw: dict[str, float] = {e["name"]: e["mol"] for e in r["produced"]}
        self.final_raw: dict[str, float] = {e["name"]: e["mol"] for e in r["final"]}
        self.H_excess_raw: float = r.get("H_excess", 0.0)

        # ---- 非 raw（化学习惯）----
        # 净离子方程式：从 steps 重建，含 H+/OH-/H2O，配平到最简整数比
        self.consumed, self.produced, self._equation = _build_ionic(r["steps"])

        # override 路径无 steps，退化为 raw（化学式层面）
        if not self.consumed and not self.produced and self.consumed_raw:
            self.consumed = dict(self.consumed_raw)
            self.produced = dict(self.produced_raw)
            self._equation = _format_equation(self.consumed, self.produced)

        # 终态离子组成：raw final + H⁺/OH⁻（H₂O 为溶剂不入）
        self.final: dict[str, float] = dict(self.final_raw)
        if self.H_excess_raw > 1e-6:
            self.final[H_ION] = self.final.get(H_ION, 0.0) + self.H_excess_raw
        elif self.H_excess_raw < -1e-6:
            self.final["OH^-"] = self.final.get("OH^-", 0.0) + (-self.H_excess_raw)

    # ---- 便捷转发 ----
    @property
    def steps(self) -> list[dict]:
        """逐步反应过程（来自 raw['steps']）。

        每项含 kind / equation / logK / S / extent / conversion 等字段。
        """
        return self.raw["steps"]

    @property
    def equation(self) -> str | None:
        """净离子方程式（如 'Zn + 2H^+ -> H_2 + Zn^{2+}'）。

        从逐步反应过程重建：解析每步 equation、按 extent 加权汇总、
        中间体自然抵消、用 H₂O 配平 H/O 原子、有理化到最简整数比。
        无显著反应时返回 None。
        """
        return self._equation

    # ---- 魔术方法 ----
    def __bool__(self) -> bool:
        return self.reacted

    def __repr__(self) -> str:
        pro = ", ".join(f"{k}×{v:.3g}" for k, v in self.produced.items())
        return (f"<Result {'反应' if self.reacted else '不反应'} "
                f"{self.degree} [{pro}] pH={self.pH}>")


# ============================================================ System

class System:
    """反应体系对象：投料量、溶液体积、温度、外界气压等参数。

    参数：
        substances  初始投料 {化学式: mol}，默认空 dict（纯水）
        V           溶液体积（L），默认 1.0
        T           温度（K），默认 298.15（25°C）；也可传 T_C 用摄氏度
        T_C         温度（°C），若给定则覆盖 T
        p           外界气压（kPa），默认 101.3（常压）；影响气体逸出阈值
        tables      自定义数据表（默认用模块级 TABLES）

    建立时（若给了 substances）与每次 add() 自动触发反应——按累计
    投入量重新平衡（化学上等价于连续投料的再平衡），返回 Result。
    全部历史结果保存在 history。
    """

    def __init__(self,
                 substances: dict[str, float] | None = None,
                 *,
                 V: float = 1.0,
                 T: float = 298.15,
                 T_C: float | None = None,
                 p: float = 101.3,
                 tables: Tables | None = None):
        self.V_L: float = float(V)
        self.T_K: float = float(T) if T_C is None else float(T_C) + 273.15
        self.p_kpa: float = float(p)
        self._tables: Tables = tables if tables is not None else TABLES
        self._feeds: dict[str, float] = {}
        self.history: list[Result] = []
        self.result: Result | None = None
        if substances:
            for name, mol in substances.items():
                self._feeds[name] = self._feeds.get(name, 0.0) + float(mol)
            self._react()

    def add(self, name: str, mol: float) -> Result:
        """加入物质（mol），自动触发反应，返回本次 Result。"""
        self._feeds[name] = self._feeds.get(name, 0.0) + float(mol)
        return self._react()

    def _react(self) -> Result:
        subs = [{"name": n, "mol": m} for n, m in self._feeds.items()]
        cond = {"V_L": self.V_L, "T_K": self.T_K, "p_kpa": self.p_kpa}
        r = judge(subs, cond, self._tables)
        self.result = Result(r)
        self.history.append(self.result)
        return self.result

    @property
    def feeds(self) -> dict[str, float]:
        """累计投料 {化学式: mol}（副本，外部修改不影响内部状态）。"""
        return dict(self._feeds)

    def __repr__(self) -> str:
        fd = ", ".join(f"{k}×{v:.3g}" for k, v in self._feeds.items())
        return f"<System [{fd}] V={self.V_L}L T={self.T_K}K p={self.p_kpa}kPa>"


# ============================================================ 一步式 API

def react(substances: dict[str, float],
          *,
          V: float = 1.0,
          T: float = 298.15,
          T_C: float | None = None,
          p: float = 101.3,
          tables: Tables | None = None) -> Result:
    """一步式反应：建立体系并立即反应，返回 Result。

    参数与 System.__init__ 一致（substances 必填）。

    示例：
        r = chemkit.react({"Zn": 1.0, "H_2SO_4": 1.0}, V=1.0)
        print(r.equation)   # 'Zn + 2H^+ -> H_2 + Zn^{2+}'
    """
    return System(substances, V=V, T=T, T_C=T_C, p=p, tables=tables).result
