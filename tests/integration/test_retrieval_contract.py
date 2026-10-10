"""检索 Agent 契约测试（工单 03）—— 用 fake retriever，进程内驱动，不碰模型/Milvus。

对齐票 06 §3/§4：入 DataPart {query, top_k} → 出 Artifact DataPart
{query, count, chunks:[{text,source,source_url,category,score}]} + 摘要 TextPart；
空结果 → COMPLETED + chunks:[]（空≠失败）；retriever 抛错 → TASK_STATE_FAILED（票 06 §7）。
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest, TaskState

from agents.retrieval.executor import RetrievalExecutor
from common import a2a_app
from common.config import load_agent

TEST_BASE = "http://testserver/"
STATE_COMPLETED = int(TaskState.TASK_STATE_COMPLETED)
STATE_FAILED = int(TaskState.TASK_STATE_FAILED)


class FakeRetriever:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    async def retrieve(self, query: str, top_k: int):
        self.calls.append((query, top_k))
        return self._chunks


class BoomRetriever:
    async def retrieve(self, query: str, top_k: int):
        raise RuntimeError("milvus down")


_TWO = [
    {"text": "支持签收后7天退货", "source": "return_policy",
     "source_url": "https://ragflow.com.cn/docs/return", "category": "general", "score": 0.91},
    {"text": "联系客服处理", "source": "contact",
     "source_url": "https://ragflow.com.cn/docs/contact", "category": "general", "score": 0.55},
]


def _make_app(retriever):
    cfg = load_agent("retrieval")
    card_cfg = dataclasses.replace(cfg.card, public_url=TEST_BASE)
    app = a2a_app.build_app(RetrievalExecutor(retriever), card_cfg)
    return app, a2a_app.to_agent_card(card_cfg)


async def _drive(app, card, payload):
    datas: list[dict] = []
    states: list[int] = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as hc:
        client = await create_client(
            agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc)
        )
        try:
            req = SendMessageRequest(message=new_data_message(payload, role=Role.ROLE_USER))
            async for chunk in client.send_message(req):
                arts = []
                if chunk.HasField("task"):
                    arts = list(chunk.task.artifacts)
                    states.append(int(chunk.task.status.state))
                elif chunk.HasField("artifact_update"):
                    arts = [chunk.artifact_update.artifact]
                elif chunk.HasField("status_update"):
                    states.append(int(chunk.status_update.status.state))
                for art in arts:
                    for d in get_data_parts(list(art.parts)):
                        if isinstance(d, dict):
                            datas.append(d)
        finally:
            await client.close()
    return datas, states


async def test_retrieval_returns_chunks_and_completes():
    r = FakeRetriever(_TWO)
    app, card = _make_app(r)
    datas, states = await _drive(app, card, {"query": "退货政策", "top_k": 3})

    assert r.calls == [("退货政策", 3)]
    payload = next((d for d in datas if "chunks" in d), None)
    assert payload is not None, f"未拿到检索结果 DataPart: {datas}"
    assert payload["query"] == "退货政策"
    assert payload["count"] == 2
    assert payload["chunks"][0]["source_url"].endswith("/docs/return")
    assert payload["chunks"][0]["score"] == 0.91
    assert states and states[-1] == STATE_COMPLETED


async def test_retrieval_empty_is_completed_with_empty_chunks():
    app, card = _make_app(FakeRetriever([]))
    datas, states = await _drive(app, card, {"query": "无关问题", "top_k": 3})

    payload = next((d for d in datas if "chunks" in d), None)
    assert payload is not None
    assert payload["count"] == 0 and payload["chunks"] == []
    assert states and states[-1] == STATE_COMPLETED


async def test_retrieval_failure_sets_failed():
    app, card = _make_app(BoomRetriever())
    _datas, states = await _drive(app, card, {"query": "x", "top_k": 3})
    assert states and states[-1] == STATE_FAILED


# ---------- 语义缓存 action 分发 ----------

class FakeCacheRetriever:
    def __init__(self, hit=None, boom=False):
        self._hit = hit; self._boom = boom; self.lookup_calls = []; self.write_calls = []

    async def retrieve(self, query, top_k):  # 保持接口完整
        return []

    async def cache_lookup(self, query, user_id, pref_sig=""):
        self.lookup_calls.append((query, user_id))
        if self._boom:
            raise RuntimeError("cache down")
        return self._hit

    async def cache_write(self, query, answer, user_id, pref_sig=""):
        self.write_calls.append((query, answer, user_id))
        if self._boom:
            raise RuntimeError("cache down")


async def test_cache_lookup_returns_hit_and_completes():
    r = FakeCacheRetriever(hit={"answer": "缓存答案", "score": 0.95})
    app, card = _make_app(r)
    datas, states = await _drive(app, card, {"action": "cache_lookup", "query": "怎么退货", "user_id": "u1"})
    assert r.lookup_calls == [("怎么退货", "u1")]
    payload = next((d for d in datas if d.get("action") == "cache_lookup"), None)
    assert payload == {"action": "cache_lookup", "hit": True, "answer": "缓存答案", "score": 0.95}
    assert states[-1] == STATE_COMPLETED


async def test_cache_lookup_miss_returns_hit_false():
    r = FakeCacheRetriever(hit=None)
    app, card = _make_app(r)
    datas, states = await _drive(app, card, {"action": "cache_lookup", "query": "q", "user_id": "u1"})
    payload = next((d for d in datas if d.get("action") == "cache_lookup"), None)
    assert payload["hit"] is False and payload["answer"] is None
    assert states[-1] == STATE_COMPLETED


async def test_cache_lookup_error_degrades_to_miss_not_failed():
    r = FakeCacheRetriever(boom=True)
    app, card = _make_app(r)
    datas, states = await _drive(app, card, {"action": "cache_lookup", "query": "q"})
    payload = next((d for d in datas if d.get("action") == "cache_lookup"), None)
    assert payload["hit"] is False
    assert states[-1] == STATE_COMPLETED      # 绝不 FAILED


async def test_cache_write_forwards_and_completes():
    r = FakeCacheRetriever()
    app, card = _make_app(r)
    datas, states = await _drive(app, card, {"action": "cache_write", "query": "q", "answer": "A", "user_id": "u1"})
    assert r.write_calls == [("q", "A", "u1")]
    payload = next((d for d in datas if d.get("action") == "cache_write"), None)
    assert payload == {"action": "cache_write", "written": True}
    assert states[-1] == STATE_COMPLETED


async def test_cache_write_error_still_completes():
    r = FakeCacheRetriever(boom=True)
    app, card = _make_app(r)
    datas, states = await _drive(app, card, {"action": "cache_write", "query": "q", "answer": "A"})
    payload = next((d for d in datas if d.get("action") == "cache_write"), None)
    assert payload["written"] is False
    assert states[-1] == STATE_COMPLETED
