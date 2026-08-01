"""chemkit 结果缓存（轻量内存 LRU）。

设计：
- 仅内存 LRU（process-local，命中延迟 < 1μs）
- 不做 SQLite 持久缓存（chemkit 启动加载已需 1-2s，持久缓存命中后再解析 JSON 反而更慢）
- 指纹算法：sha256(sorted(reactants) + sorted(conditions))
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from app.models.db import compute_fingerprint


class _LRUCache:
    """异步锁保护的 LRU 缓存。"""

    def __init__(self, capacity: int = 2048) -> None:
        self._capacity = capacity
        self._data: OrderedDict[str, dict] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> dict | None:
        async with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._hits += 1
                return self._data[key]
            self._misses += 1
            return None

    async def put(self, key: str, value: dict) -> None:
        async with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def stats(self) -> dict[str, int | float]:
        total = self._hits + self._misses
        return {
            "size": len(self._data),
            "capacity": self._capacity,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total else 0.0,
        }


# 全局单例
_memory_cache = _LRUCache(capacity=2048)


async def cache_get(reactants: list[tuple[str, float]], conditions: dict) -> dict | None:
    """查询内存缓存。"""
    fp = compute_fingerprint(reactants, conditions)
    return await _memory_cache.get(fp)


async def cache_put(reactants: list[tuple[str, float]], conditions: dict, result: dict) -> None:
    """写入内存缓存。"""
    fp = compute_fingerprint(reactants, conditions)
    await _memory_cache.put(fp, result)


def cache_stats() -> dict[str, Any]:
    """返回缓存统计信息。"""
    return {"memory": _memory_cache.stats()}


__all__ = ["cache_get", "cache_put", "cache_stats"]
