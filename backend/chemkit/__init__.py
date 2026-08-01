"""chemkit：水溶液反应判定引擎（离子基、高中~竞赛水平）。

import chemkit 即自动加载数据表（chemkit.system.TABLES）。

高层 API（反应体系对象）：
    import chemkit
    sys = chemkit.System(V=1.0, T=298.15, p=101.3)   # 空体系（纯水）
    r = sys.add("NaOH", 0.1)                          # 自动反应，返回 Result
    r = chemkit.react({"NaOH": 0.1, "HCl": 0.1}, V=1.0)   # 一步式

    r.consumed, r.produced, r.pH, r.equations        # 直观字段 + 配平方程式

底层 API：
    from chemkit import load_tables, judge
    T = load_tables()                 # 默认加载包内 ./data
    r = judge([{"name": "HCl", "mol": 1.0}, {"name": "NaOH", "mol": 1.0}],
              {"V_L": 1.0, "T_K": 298.15, "p_kpa": 101.3}, T)

辅助 API（化学式与方程式）：
    from chemkit import FormulaError, balance
    balance(["H_2", "O_2"], ["H_2O"])  # -> {"reactants": {...}, "products": {...}}

测试套件为独立模块 chemkit.testsuit（默认不加载）：
    python -m chemkit.testsuit        # 运行 data/tests.json 全部用例 + 环闭合检查
"""
from .data import Tables, load_tables
from .engine import judge
from .system import Result, System, react, default_tables, TABLES
from .core import FormulaError, balance

__version__ = "2.6.0"
__all__ = ["Tables", "load_tables", "judge",
           "Result", "System", "react", "default_tables",
           "TABLES", "FormulaError", "balance", "__version__"]
