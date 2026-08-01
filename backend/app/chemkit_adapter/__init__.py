"""chemkit 适配层。

将 chemkit 同步函数包装为异步调用，提供：
- 内存 LRU 缓存
- 反应预演（dry-run，不写入游戏状态，用于 AI 与合法性校验）
- 启动时预热 chemkit 的温度相关缓存
"""

from __future__ import annotations

from app.chemkit_adapter.adapter import (
    dry_run,
    init_chemkit,
    is_loaded,
    list_products,
    react,
    warmup,
)
from app.chemkit_adapter.cache import cache_stats
from app.chemkit_adapter.models import Conditions, Degree, ReactionResultData, Reactant

__all__ = [
    "react",
    "dry_run",
    "list_products",
    "warmup",
    "init_chemkit",
    "is_loaded",
    "cache_stats",
    "Conditions",
    "Reactant",
    "ReactionResultData",
    "Degree",
]
