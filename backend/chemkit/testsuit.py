"""chemkit.testsuit：统一测试模块（默认不随包加载，__init__ 不导出）。

运行：
    python -m chemkit.testsuit                # 默认 ./data/tests.json + 环闭合检查
    python -m chemkit.testsuit path/to.json   # 指定用例库

内容：
  1) 用例运行器：加载 tests.json（默认包内 ./data/tests.json），逐例调 judge
     并校验 reacted/degree/ann/override/has/has_not/has_range/has_any/ph。
  2) 温度域校验（4 项）：273.15–373.15K 域外必须报错不静默。
  3) 热力学环闭合检查（原 checks/consistency.py 并入）：任何数据不许单点
     存在，必须能在 Hess 网络里自洽。
"""
from __future__ import annotations
import json
import os
import sys
import time

from .data import load_tables, Tables, _half_balance
from .engine import judge
from .core import charge_of

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DEFAULT_CASES = os.path.join(DATA_DIR, "tests.json")

PASS_N = 0
FAILS: list[str] = []
TIMES: list[tuple[float, str]] = []   # 逐例计时（秒，用例名）


def load_cases(path: str | None = None) -> list[dict]:
    """加载测试用例库（默认包内 ./data/tests.json）。"""
    with open(path or DEFAULT_CASES, encoding="utf-8") as f:
        return json.load(f)


def run_case(c: dict, T: Tables, verbose: bool = True) -> bool:
    """运行单条用例并校验全部断言维度；返回是否通过。"""
    global PASS_N
    name = c["name"]
    subs = [{"name": n, "mol": m} for n, m in c["subs"]]
    t0 = time.time()
    r = judge(subs, c.get("cond") or {"V_L": 1.0}, T)
    TIMES.append((time.time() - t0, name))
    errs = []
    # 量值校验统一用 最终态∪产物 的合并视图
    amt = {}
    for e in r["produced"] + r["final"]:
        amt[e["name"]] = max(amt.get(e["name"], 0.0), e["mol"])
    if "reacted" in c and r["reacted"] != c["reacted"]:
        errs.append(f"reacted={r['reacted']} 期望{c['reacted']}")
    if "degree" in c:
        exp = c["degree"] if isinstance(c["degree"], list) else [c["degree"]]
        if r["degree"] not in exp:
            errs.append(f"degree={r['degree']} 期望{exp}")
    for a in c.get("ann", []):
        if a not in r["annotations"]:
            errs.append(f"缺标注 {a}")
    if "override" in c and r.get("override") != c["override"]:
        errs.append(f"override={r.get('override')} 期望{c['override']}")
    for sp, lo in c.get("has", {}).items():
        m = amt.get(sp, 0.0)
        if m < lo:
            errs.append(f"{sp} 产量 {m:.4g} < {lo}")
    for sp, hi in c.get("has_not", {}).items():
        m = amt.get(sp, 0.0)
        if m > hi:
            errs.append(f"{sp} 不应生成 {m:.4g} > {hi}")
    for sp, (lo, hi) in c.get("has_range", {}).items():
        m = amt.get(sp, 0.0)
        if not (lo <= m <= hi):
            errs.append(f"{sp}={m:.4g} 不在 [{lo},{hi}]")
    for sp, lo in c.get("has_any", {}).items():
        m = amt.get(sp, 0.0)
        if m < lo:
            errs.append(f"{sp}(any) 产量 {m:.4g} < {lo}")
    if "ph" in c and r["final_pH"] is not None:
        if not (c["ph"][0] <= r["final_pH"] <= c["ph"][1]):
            errs.append(f"ph={r['final_pH']} 不在 {c['ph']}")
    if errs:
        FAILS.append(name)
        if verbose:
            print(f"[FAIL] {name}  -- {'; '.join(errs)}"
                  + (f"  ({c['note']})" if c.get("note") else ""))
            print("   steps:", [(s["equation"], s["extent"]) for s in r["steps"][:6]])
            print("   produced:", [(p["name"], p["mol"]) for p in r["produced"]],
                  "| pH:", r["final_pH"], "| degree:", r["degree"],
                  "| ann:", r["annotations"])
        return False
    PASS_N += 1
    if verbose:
        print(f"[PASS] {name}" + (f"  ({c['note']})" if c.get("note") else ""))
    return True


def case_T_range(T: Tables) -> int:
    """温度域与气压校验：
       - 273.15–373.15K 之外（非常压液态水）必须报错不静默
       - p_kpa <= 0 必须报错（负压/零压无物理意义）
    返回通过数。"""
    global PASS_N
    for tk, ok in [(250.0, False), (400.0, False), (273.15, True), (373.15, True)]:
        try:
            judge([], {"T_K": tk}, T)
            if ok:
                PASS_N += 1
            else:
                FAILS.append(f"T_K={tk} 未报错")
        except ValueError:
            if ok:
                FAILS.append(f"T_K={tk} 被误拒")
            else:
                PASS_N += 1
    # p_kpa 边界：0 / 负数必须报错；正常气压应通过
    for pk, ok in [(0.0, False), (-10.0, False), (50.0, True), (101.3, True), (500.0, True)]:
        try:
            judge([{"name": "NaCl", "mol": 0.01}], {"p_kpa": pk}, T)
            if ok:
                PASS_N += 1
            else:
                FAILS.append(f"p_kpa={pk} 未报错")
        except ValueError:
            if ok:
                FAILS.append(f"p_kpa={pk} 被误拒")
            else:
                PASS_N += 1
    return PASS_N


# ============================================================ 环闭合检查
PKW = 14.0
K = 0.05916


def consistency(T: Tables, verbose: bool = True) -> list[str]:
    """热力学环闭合检查（数据纪律：任何数据不许单点存在）。返回失败列表。"""
    fails = []

    def check(name, lhs, rhs, tol=0.05):
        if abs(lhs - rhs) > tol:
            fails.append(f"[FAIL] {name}: {lhs:.3f} vs {rhs:.3f} "
                         f"(差 {abs(lhs - rhs):.3f} > {tol})")
        elif verbose:
            print(f"[ok] {name}: {lhs:.3f} ≈ {rhs:.3f}")

    # 1) h 形电对与 oh 文献参考值的换算闭合：E_h − E_oh = k·(h/n)·pKw
    #    （h 不入库，运行时配平重算）
    for c in T.couples:
        if "oh_ref" in c:
            bal = _half_balance(c["ox"], c["red"])
            expect = K * ((bal[1] if bal else 0) / c["n"]) * PKW
            check(f"电对 h/oh 换算 {c['ox']}/{c['red']}",
                  c["E0"] - c["oh_ref"], expect, 0.02)

    # 2) 多元酸分级 pKa 之和 = 合并条目
    for e_sum in T.pka:
        if e_sum["n"] >= 2:
            chain = [e for e in T.pka if e["n"] == 1 and
                     (e["acid"] == e_sum["acid"] or e["base"] == e_sum["base"])]
            if len(chain) >= e_sum["n"]:
                s = sum(sorted([e["pka"] for e in chain])[:e_sum["n"]])
                check(f"多元酸 pKa 和 {e_sum['acid']}→{e_sum['base']}",
                      s, e_sum["pka"], 0.3)

    # 3) 配位电对 E0 与 logβ 自洽：E(complex/M) = E(M^n+/M) − k·logβ/n
    for c in T.couples:
        if c["ox"] in T.beta_by_complex:
            b = T.beta_by_complex[c["ox"]]
            ref = next((x for x in T.couples
                        if x["ox"] == b["center"] and x["red"] == c["red"]), None)
            if ref:
                check(f"配位电对 {c['ox']}/{c['red']} ↔ logβ",
                      c["E0"], ref["E0"] - K * b["logb"] / c["n"], 0.05)

    # 4) Fe(OH)3/Fe(OH)2 与 Fe3+/Fe2+ 及两侧 Ksp 自洽
    fe = next(c for c in T.couples
              if c["ox"] == "Fe(OH)_3" and c["red"] == "Fe(OH)_2")
    fe_ion = next(c for c in T.couples
                  if c["ox"] == "Fe^{3+}" and c["red"] == "Fe^{2+}")
    k3 = T.ksp_by_solid["Fe(OH)_3"]["pKsp"]
    k2 = T.ksp_by_solid["Fe(OH)_2"]["pKsp"]
    check("Fe(OH)3/Fe(OH)2 oh 形 ↔ Ksp 网络",
          fe["oh_ref"], fe_ion["E0"] - K * (k3 - k2), 0.05)

    # 5) 氢氧化物 Ksp 与"阳离子水解 pKa"等价（单一数据源声明，打印备查）
    if verbose:
        for e in T.ksp:
            if e["pair"][1] == "OH^-":
                n = charge_of(e["pair"][0])
                print(f"[info] {e['solid']}: 水解 logK = pKsp − n·pKw = "
                      f"{e['pKsp'] - n * PKW:.1f} "
                      f"（{e['pair'][0]} + {n}H2O ⇌ {e['solid']} + {n}H+）")

    # 6) 氨合配离子溶解度梯度（Ksp⊗β 派生 logK：AgCl 微负、AgBr 更负、AgI 极负）
    if verbose:
        for solid in ("AgCl", "AgBr", "AgI"):
            e = T.ksp_by_solid[solid]
            b = T.beta_by_complex["[Ag(NH_3)_2]^+"]
            print(f"[info] {solid} + 2NH3 ⇌ 配离子 + 卤离子: "
                  f"logK = {b['logb'] - e['pKsp']:.2f}")

    return fails


def dH_coverage(T: Tables) -> None:
    """dH 运行时派生覆盖率报告（原 build_dH.py 职责并入）。"""
    print("\n---- dH 覆盖率 ----")
    for name, tbl in (("couples", T.couples), ("pka", T.pka),
                      ("ksp", T.ksp), ("beta", T.beta)):
        hit = sum(1 for e in tbl if e.get("dH") is not None)
        print(f"  {name}: {hit}/{len(tbl)}")


def case_api() -> None:
    """System/Result 高层 API 自检（6 项）：对象语义、累计投料再平衡、
    raw 过程保留、equation 净离子方程式、H+/OH-/H2O 显式出现、外界气压调节。"""
    import chemkit
    global PASS_N
    sys = chemkit.System(V=1.0)
    sys.add("NaOH", 0.1)
    r2 = sys.add("HCl", 0.1)
    ok1 = (isinstance(r2, chemkit.Result) and r2.reacted
           and r2.degree == "complete" and r2.pH is not None
           and 6.0 <= r2.pH <= 8.0 and len(sys.history) == 2
           and sys.feeds.get("NaOH") == 0.1)
    r3 = chemkit.react({"Ca(OH)_2": 1.0, "CO_2": 0.5}, V=1.0)
    ok2 = (r3.produced.get("CaCO_3", 0.0) >= 0.45
           and isinstance(r3.raw.get("steps"), list))
    ok3 = chemkit.System({"NaCl": 0.1}).result is not None
    # 净离子方程式：Zn + H2SO4 → Zn + 2H+ -> H2 + Zn2+
    r4 = chemkit.react({"Zn": 1.0, "H_2SO_4": 1.0}, V=1.0)
    ok4 = (isinstance(r4.equation, str) and "Zn" in r4.equation
           and "H^+" in r4.equation and "H_2" in r4.equation)
    # H+/OH-/H2O 显式出现在 consumed/produced：
    # NaOH+HCl → consumed 含 H+ 和 OH-，produced 含 H2O
    r5 = chemkit.react({"NaOH": 0.1, "HCl": 0.1}, V=1.0)
    ok5 = ("H^+" in r5.consumed and "OH^-" in r5.consumed
           and "H_2O" in r5.produced)
    # 外界气压调节：低压下气体更易逸出（不应报错）
    r6 = chemkit.react({"Na_2CO_3": 0.1, "HCl": 0.2}, V=1.0, p=50.0)
    ok6 = r6.reacted and "CO_2" in r6.produced
    for tag, ok in (("API System 累计投料再平衡", ok1),
                    ("API react 一步式+raw 过程", ok2),
                    ("API System 建立即反应", ok3),
                    ("API Result.equation 净离子方程式", ok4),
                    ("API H+/OH-/H2O 显式出现", ok5),
                    ("API 外界气压 p 调节", ok6)):
        if ok:
            PASS_N += 1
        else:
            FAILS.append(tag)


def main(cases_path: str | None = None) -> int:
    T = load_tables()
    judge([{"name": "NaCl", "mol": 0.1}], {"V_L": 1.0}, T)   # 预热：模板/缓存冷启动不计入首例
    TIMES.clear()
    for c in load_cases(cases_path):
        run_case(c, T)
    case_T_range(T)
    case_api()
    total = PASS_N + len(FAILS)
    print(f"\n===== {PASS_N}/{total} PASS =====")
    if FAILS:
        print("失败:", FAILS)

    slow = sorted((t, n) for t, n in TIMES if t > 0.5)
    if slow:
        print(f"\n---- 慢用例（>{0.5}s，共 {len(slow)} 例）----")
        for t, n in reversed(slow):
            print(f"  {t:6.2f}s {n}")
    print(f"总耗时 {sum(t for t, _ in TIMES):.1f}s")
    dH_coverage(T)

    print("\n---- 环闭合检查 ----")
    cfail = consistency(T)
    if cfail:
        print("\n".join(cfail))
        return 1
    print("全部环闭合检查通过。")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
