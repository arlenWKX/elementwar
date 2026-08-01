"""chemkit 适配器：异步封装。

核心问题：
- chemkit 是同步阻塞的（5-50ms 典型，复杂多步可能 100ms+）
- Socket.IO 在 asyncio 事件循环中，不能阻塞

解决方案：
- 用 `asyncio.to_thread` 把同步调用隔离到默认线程池
- chemkit 的 Tables 单例在多线程下首次写不安全，启动时预热避免运行时 lazy population
- 全局 `threading.Lock` 兜底，防止 Tables._* 缓存的并发首写竞争
- 内存 LRU 缓存命中即返回，避免重复计算
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from app.chemkit_adapter.cache import cache_get, cache_put
from app.chemkit_adapter.models import Conditions, ReactionResultData, Reactant
from app.config import settings
from app.services.logger import get_logger

logger = get_logger(__name__)


# chemkit Tables 全局锁（防止 lazy 缓存并发首写）
_chemkit_lock = threading.Lock()

# chemkit 引擎句柄（懒加载，启动后只读）
_tables = None


def _load_tables():
    """加载 chemkit Tables（仅执行一次）。"""
    global _tables
    if _tables is not None:
        return _tables
    import chemkit

    data_dir = Path(settings.chemkit_data_dir).resolve()
    if not data_dir.exists():
        logger.warning("chemkit.data_dir_missing", extra={"path": str(data_dir)})
        _tables = chemkit.default_tables()
    else:
        _tables = chemkit.load_tables(str(data_dir))
    logger.info("chemkit.tables_loaded", extra={"data_dir": str(data_dir)})
    return _tables


def is_loaded() -> bool:
    """chemkit Tables 是否已加载。"""
    return _tables is not None


def warmup() -> None:
    """启动时预热 chemkit。

    1. 加载 Tables（触发所有 JSON 解析 + Hess ΔH 推导）
    2. 跑一个简单反应触发 _redox_tmpl[298.15] 等温度相关缓存填充
    """
    import chemkit

    tables = _load_tables()
    with _chemkit_lock:
        chemkit.judge(
            [{"name": "NaCl", "mol": 0.1}],
            {"V_L": 1.0, "T_K": 298.15, "p_kpa": 101.3},
            tables,
        )
    logger.info("chemkit.warmup.done", extra={"version": chemkit.__version__})


# ============================================================
# 核心异步接口
# ============================================================
async def react(
    reactants: list[Reactant],
    conditions: Conditions | None = None,
    *,
    use_cache: bool = True,
) -> ReactionResultData:
    """异步执行反应计算。

    Args:
        reactants: 反应物列表
        conditions: 反应条件，None 用默认 (1L, 298.15K, 101.3kPa)
        use_cache: 是否使用缓存（默认开启）

    Returns:
        ReactionResultData 强类型结果
    """
    cond = conditions or Conditions()
    cond_dict = cond.to_dict()
    reactant_tuples: list[tuple[str, float]] = [(r.name, r.mol) for r in reactants]
    substance_dicts = [r.to_dict() for r in reactants]

    # 1. 查缓存
    if use_cache:
        cached = await cache_get(reactant_tuples, cond_dict)
        if cached is not None:
            return ReactionResultData.from_chemkit(cached, duration_ms=0.1, cached=True)

    # 2. 同步调用，隔离到线程池
    start = time.perf_counter()
    tables = _load_tables()
    try:
        raw = await asyncio.to_thread(_judge_sync, substance_dicts, cond_dict, tables)
    except ValueError as e:
        logger.warning("chemkit.invalid_input", extra={"err": str(e)})
        return ReactionResultData(
            reacted=False,
            degree="none",
            consumed={},
            produced={},
            final={},
            ph=None,
            equation=None,
            annotations=[],
            override=None,
            unknown=[],
            steps=[],
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    elapsed_ms = (time.perf_counter() - start) * 1000

    # 3. 写缓存
    if use_cache and raw.get("reacted"):
        await cache_put(reactant_tuples, cond_dict, raw)

    return ReactionResultData.from_chemkit(raw, duration_ms=elapsed_ms)


def _judge_sync(substances: list[dict], cond: dict, tables) -> dict:
    """同步调用 chemkit.judge，加进程级锁。"""
    import chemkit
    with _chemkit_lock:
        return chemkit.judge(substances, cond, tables)


async def dry_run(
    reactants: list[Reactant],
    conditions: Conditions | None = None,
) -> ReactionResultData:
    """反应预演：不写缓存，仅用于 AI 决策与合法性预校验。"""
    return await react(reactants, conditions, use_cache=False)


async def list_products(
    reactants: list[Reactant],
    conditions: Conditions | None = None,
) -> list[str]:
    """列出可能的产物名称（前端高亮"可打"的牌用，AI 决策用）。

    排除与反应物同名的（即未消耗的剩余反应物）。

    对于中和反应等 produced 为空的场景，用 final 中的非环境物种作为候选。
    """
    result = await dry_run(reactants, conditions)
    if not result.reacted:
        return []
    reactant_names = {r.name for r in reactants}
    products = list(result.produced.keys())
    if not products:
        # 中和反应等场景：用 final 中的非环境物种
        env_species = {"H_2O", "O^{2-}", "H^+", "OH^-"}
        products = [
            name for name in result.final.keys()
            if name not in env_species and name not in reactant_names
        ]
    return [name for name in products if name not in reactant_names]


# ============================================================
# 启动钩子
# ============================================================
async def init_chemkit() -> None:
    """FastAPI startup 钩子：加载 + 预热。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, warmup)


__all__ = [
    "react",
    "dry_run",
    "list_products",
    "warmup",
    "init_chemkit",
    "is_loaded",
    "cache_stats",
]
