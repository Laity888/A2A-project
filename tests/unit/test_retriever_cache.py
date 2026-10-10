"""检索结果缓存单测（工单：检索去重缓存）。

不起真模型：用 object.__new__ 造 Retriever 裸实例、只喂缓存相关字段与一个假
_retrieve_sync，验证 ① 同 (query,k) 第二次走缓存不重跑推理；② LRU 淘汰最久未用；
③ 禁用缓存时每次都推理；④ 命中返回的是拷贝、不被调用方改动污染。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from agents.retrieval.retriever import Retriever


def _bare(cache_enabled=True, cache_max=2):
    r = object.__new__(Retriever)
    r.top_k = 8
    r._cache_enabled = cache_enabled
    r._cache_max = cache_max
    r._cache = OrderedDict()
    r._sem = asyncio.Semaphore(1)
    r._calls = []

    def _fake_sync(query, k):
        r._calls.append((query, k))
        return [{"text": query, "source_url": "http://x", "score": 1.0}]

    r._retrieve_sync = _fake_sync
    return r


async def test_retrieve_second_call_hits_cache():
    r = _bare()
    a = await r.retrieve("退货")
    b = await r.retrieve("退货")
    assert r._calls == [("退货", 8)]   # 只推理一次
    assert a[0]["text"] == "退货" and b[0]["text"] == "退货"


async def test_different_k_not_shared_in_cache():
    r = _bare(cache_max=8)
    await r.retrieve("退货", top_k=3)
    await r.retrieve("退货", top_k=8)
    assert r._calls == [("退货", 3), ("退货", 8)]


async def test_cache_hit_returns_copy():
    r = _bare()
    a = await r.retrieve("退款")
    a[0]["text"] = "被调用方篡改"       # 改动返回值不应污染缓存
    b = await r.retrieve("退款")
    assert b[0]["text"] == "退款"


async def test_lru_evicts_least_recently_used():
    r = _bare(cache_max=2)
    r._cache_put(("a", 8), [{"t": "a"}])
    r._cache_put(("b", 8), [{"t": "b"}])
    assert r._cache_get(("a", 8)) is not None   # a 变为最近使用
    r._cache_put(("c", 8), [{"t": "c"}])         # 超上限 → 淘汰 b
    assert ("b", 8) not in r._cache
    assert ("a", 8) in r._cache and ("c", 8) in r._cache


async def test_cache_disabled_always_runs():
    r = _bare(cache_enabled=False)
    await r.retrieve("退货")
    await r.retrieve("退货")
    assert r._calls == [("退货", 8), ("退货", 8)]
