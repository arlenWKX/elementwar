"""chemkit.core：基础函数库——化学式解析（formula）、方程式配平（balance）、
热力学常数与温度函数（Nernst/van't Hoff/pKw）。数据加载与 Hess 派生见 data.py。
"""
from __future__ import annotations
from math import gcd



# ============================================================ formula
import re
from functools import lru_cache

CHARGE_RE = re.compile(r"\^\{?(\d*)([+-])\}?$")


class FormulaError(ValueError):
    pass


@lru_cache(maxsize=None)
def parse_species(name: str) -> tuple:
    """返回 (elements_tuple, charge)。"""
    elems, charge = _parse(name)
    return (tuple(sorted(elems.items())), charge)


def elements_of(name: str) -> dict:
    return dict(parse_species(name)[0])


def charge_of(name: str) -> int:
    return parse_species(name)[1]


def _parse(name: str) -> tuple[dict, int]:
    s = name.strip()
    if not s:
        raise FormulaError("empty species name")
    # 1. 末尾上标电荷（必须 ^ 引导）
    charge = 0
    m = CHARGE_RE.search(s)
    if m:
        charge = int(m.group(1) or "1") * (1 if m.group(2) == "+" else -1)
        s = s[: m.start()]
    # 2. 水合点
    if "·" in s or "." in s:
        parts = re.split(r"[·.]", s)
        total: dict = {}
        for p in parts:
            p = p.strip()
            mm = re.match(r"^(\d+)(.*)$", p)
            coef, rest = (int(mm.group(1)), mm.group(2)) if mm else (1, p)
            sub, q = _parse(rest)
            if q != 0:
                raise FormulaError(f"hydrate part carries charge: {name}")
            for k, v in sub.items():
                total[k] = total.get(k, 0) + coef * v
        return total, 0
    # 3. 主体
    elems = _parse_body(s)
    return elems, charge


def _read_count(s: str, i: int) -> tuple[int, int]:
    """读可选计数：_{n} / _n / 裸数字；无则 1。"""
    if i < len(s) and s[i] == "_":
        i += 1
        if i < len(s) and s[i] == "{":
            j = s.index("}", i)
            return int(s[i + 1 : j]), j + 1
        m = re.match(r"\d+", s[i:])
        if not m:
            raise FormulaError(f"expected digits after _ in {s}")
        return int(m.group(0)), i + len(m.group(0))
    m = re.match(r"\d+", s[i:])
    if m:
        return int(m.group(0)), i + len(m.group(0))
    return 1, i


def _parse_body(s: str) -> dict:
    def parse_group(i: int, closing: str | None) -> tuple[dict, int]:
        buf: dict = {}
        while i < len(s):
            ch = s[i]
            if ch in "([":
                sub, i = parse_group(i + 1, ")" if ch == "(" else "]")
                n, i = _read_count(s, i)
                for k, v in sub.items():
                    buf[k] = buf.get(k, 0) + n * v
            elif closing and ch == closing:
                return buf, i + 1
            elif ch.isupper():
                j = i + 1
                while j < len(s) and s[j].islower():
                    j += 1
                el = s[i:j]
                n, i = _read_count(s, j)
                buf[el] = buf.get(el, 0) + n
            else:
                raise FormulaError(f"bad char {ch!r} in {s}")
        if closing:
            raise FormulaError(f"unclosed {closing} in {s}")
        return buf, i

    out, pos = parse_group(0, None)
    if pos != len(s):
        raise FormulaError(f"unparsed tail in {s}")
    return out


# ============================================================ balance
from fractions import Fraction
from functools import reduce
import sympy as sp


def balance(reactants: list[str], products: list[str],
            free: list[str] | None = None) -> dict | None:
    free = free or []
    species = list(dict.fromkeys(list(reactants) + list(products) + list(free)))
    elems: list[str] = sorted({e for s in species for e in elements_of(s)})
    rows = [[Fraction(elements_of(s).get(el, 0)) for s in species] for el in elems]
    rows.append([Fraction(charge_of(s)) for s in species])
    M = sp.Matrix(rows)   # Fraction 精确零空间（float 会产生不守恒伪向量）
    ns = M.nullspace()
    # 候选向量：基向量 + 小整数组合（零空间维数 ≥2 时，全物种参与的合法解
    # 可能是基向量的线性组合而非基向量本身，如 MnO4-/H2O2 体系）
    vecs = list(ns)
    if len(ns) >= 2:
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                for a in (1, -1, 2, -2, 3, -3):
                    vecs.append(ns[i] + a * ns[j])
    for vec in vecs:
        res = _try_vec(vec, species, reactants, products, free, elems)
        if res is not None:
            return res
    return None


def _try_vec(vec, species, reactants, products, free, elems) -> dict | None:
    ints = _to_ints(vec)
    if ints is None:
        return None
    coef = dict(zip(species, ints))
    # 组一致性：coef 符号决定物种真实侧；同时在两侧的指定物种（如配体=阴离子）
    # 只按符号归属一次，不参与另一侧检查
    both = set(reactants) & set(products)
    # 指定物种（非交集、非环境物种）系数必须非零：零系数意味着净方程实际未
    # 涉及该物种（如 MnO4- + H2O2 配出仅 H2O2 -> O2 的子系统解，属错解）；
    # 环境物种（H2O/H+，在 free 中）允许系数为零（净方程抵消，如 Zn+水）
    for s in list(reactants) + list(products):
        if s not in both and s not in free and coef.get(s, 0) == 0:
            return None

    def group_signs(group: list[str]) -> set[int]:
        out: set[int] = set()
        for s in group:
            if s in both:      # 交集物种符号唯一，构建时按号归位，不参与检查
                continue
            v = coef[s]
            if v != 0:
                out.add(1 if v > 0 else -1)
        return out

    r_signs = group_signs(reactants)
    p_signs = group_signs(products)
    if len(r_signs) != 1 or len(p_signs) != 1:
        return None
    r_sign = next(iter(r_signs))
    p_sign = next(iter(p_signs))
    if r_sign == p_sign:
        return None
    if r_sign < 0:
        coef = {k: -v for k, v in coef.items()}
    # 构建两侧：非零系数按号归位
    res: dict = {"reactants": {}, "products": {}}
    for s in dict.fromkeys(reactants):
        if coef[s] > 0:
            res["reactants"][s] = res["reactants"].get(s, 0) + coef[s]
        elif coef[s] < 0 and s not in both:
            return None
    for s in dict.fromkeys(products):
        if coef[s] < 0:
            res["products"][s] = res["products"].get(s, 0) - coef[s]
        elif coef[s] > 0 and s not in both:
            return None
    for s in free:
        if s in reactants or s in products:
            continue  # 已在指定侧记账，防止重复累加
        v = coef.get(s, 0)
        if v > 0:
            res["reactants"][s] = res["reactants"].get(s, 0) + v
        elif v < 0:
            res["products"][s] = res["products"].get(s, 0) - v
    # 守恒断言（防御数值误差）：元素与电荷两侧必须相等
    for el in elems:
        lhs = sum(elements_of(s).get(el, 0) * n for s, n in res["reactants"].items())
        rhs = sum(elements_of(s).get(el, 0) * n for s, n in res["products"].items())
        if lhs != rhs:
            return None
    lhs = sum(charge_of(s) * n for s, n in res["reactants"].items())
    rhs = sum(charge_of(s) * n for s, n in res["products"].items())
    if lhs != rhs:
        return None
    return res


def _to_ints(vec) -> list[int] | None:
    fracs = [Fraction(v).limit_denominator(1000) for v in vec]
    den = reduce(lambda a, b: a * b // gcd(a, b), (f.denominator for f in fracs), 1)
    ints = [int(f * den) for f in fracs]
    g = reduce(gcd, (abs(i) for i in ints if i != 0), 0)
    if g == 0:
        return None
    return [i // g for i in ints]


# ---------------------------------------------------------- 热力学基础函数
K_NERNST_298 = 0.05916          # Nernst 斜率 (V, 298 K)
PKW_298 = 14.0
R_LN10_KJ = 8.314462618e-3 * 2.302585093   # R·ln10，kJ/(mol·K)


def k_nernst(T_K: float) -> float:
    return K_NERNST_298 * T_K / 298.15


def _vant(dH: float, T_K: float) -> float:
    """van't Hoff 温度修正（ΔCp≈0）：logK(T) += dH/(R·ln10)·(1/298.15 − 1/T)。
    dH 单位 kJ/mol，298.15 K 恒等零。液态水温度域（273–373 K）内精度
    ±0.1~0.3 logK；dH 由 core 在加载时按 Hess 定律从 thermo.json 派生。"""
    return dH / R_LN10_KJ * (1.0 / 298.15 - 1.0 / T_K)


def pKw_of(T_K: float) -> float:
    """pKw(T) 经验式：298.15 K -> 14.0，373 K -> 12.3。"""
    return 4471.0 / T_K - 6.09 + 0.0171 * T_K


