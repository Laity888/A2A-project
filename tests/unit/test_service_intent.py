"""意图识别离线测试（#15–#19）：分流枚举解析 + 接待按意图路由。

不连真 LLM/DB：
- TriageAgent.classify 解析四类枚举、派生 is_service、异常/不可解析保守 knowledge、旧输出兼容；
- ReceptionService.stream 按 intent 路由：chitchat 跳过检索/缓存/历史、transaction 走链路但不缓存、
  out_of_scope 兜底、knowledge 正常缓存。
"""

from __future__ import annotations

import json

from agents.reception.answer_cache import AnswerCache
from agents.reception.service import ReceptionService
from agents.reception.triage import TriageAgent

FALLBACK = "抱歉，我目前只能处理客服/工单相关的问题。"
KB = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'


# ---------- TriageAgent 解析 ----------

class ChatLLM:
    def __init__(self, reply, raise_it=False):
        self.reply = reply; self.raise_it = raise_it
    async def chat(self, messages):
        if self.raise_it:
            raise RuntimeError("down")
        return self.reply


def _triage(llm):
    return TriageAgent(llm, {"triage": "P"})


async def test_triage_parses_enum_and_derives_is_service():
    for intent, exp_service in [("chitchat", True), ("knowledge", True),
                                ("transaction", True), ("out_of_scope", False)]:
        llm = ChatLLM(json.dumps({"intent": intent, "confidence": 0.9, "reason": "r"}))
        v = await _triage(llm).classify("x")
        assert v["intent"] == intent
        assert v["is_service"] is exp_service


async def test_triage_error_conservative_knowledge():
    v = await _triage(ChatLLM("", raise_it=True)).classify("x")
    assert v["intent"] == "knowledge" and v["is_service"] is True


async def test_triage_unparsed_conservative_knowledge():
    v = await _triage(ChatLLM("这不是 JSON")).classify("x")
    assert v["intent"] == "knowledge" and v["is_service"] is True


async def test_triage_legacy_is_service_bool_still_works():
    v = await _triage(ChatLLM(json.dumps({"is_service": False, "intent": "chat", "reason": "r"}))).classify("x")
    assert v["is_service"] is False and v["intent"] == "chat"
    v2 = await _triage(ChatLLM(json.dumps({"is_service": True, "intent": "return", "reason": "r"}))).classify("x")
    assert v2["is_service"] is True and v2["intent"] == "return"


# ---------- 接待按意图路由 ----------

class TriageStub:
    def __init__(self, intent, is_service=True):
        self.intent = intent; self.is_service = is_service
    async def classify(self, text):
        return {"intent": self.intent, "is_service": self.is_service, "confidence": 0.9, "reason": "r"}


class LLM:
    async def chat_tools(self, messages, tools):
        return {"content": "", "tool_calls": []}   # 不调工具 → 全链路时兜底补 kb_search


class Broker:
    def __init__(self, results=None): self.results = results or {}; self.calls = []
    async def list_tools(self): return []
    async def call(self, name, args, context_id=None):
        self.calls.append((name, args)); return self.results.get(name, '{"count":0,"chunks":[]}')


class Reply:
    def __init__(self, deltas): self.deltas = deltas; self.calls = []
    async def reply_stream(self, query, chunks, history, context_id=None, memory=None, mode=None):
        self.calls.append({"chunks": chunks, "mode": mode, "memory": memory})
        for d in self.deltas:
            yield d


class Session:
    def __init__(self): self.appended = []
    async def history(self, cid): return []
    async def append(self, cid, u, a, trace=None): self.appended.append((u, a))


class Mem:
    def __init__(self, profile=None): self.profile = dict(profile or {}); self.appended = []
    def get_profile(self, user_id): return dict(self.profile)
    def get_recent_history(self, user_id, n=None): return []
    def append_history(self, user_id, intent, query, context_id=None):
        self.appended.append((user_id, intent, query, context_id))


async def _tokens(svc, text, cid="c1", uid="u1"):
    out = []
    async for ev in svc.stream(text, cid, user_id=uid):
        if ev["ev"] == "token":
            out.append(ev["text"])
    return out


def _svc(triage, reply, broker, mem, cache):
    return ReceptionService(
        llm=LLM(), triage=triage, tool_broker=broker, reply_client=reply, session=Session(),
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4, answer_cache=cache, memory=mem,
    )


async def test_chitchat_skips_retrieval_cache_and_history():
    reply = Reply(["你好呀"])
    broker = Broker()
    mem = Mem(profile={"reply_style": "古风"})
    cache = AnswerCache()
    svc = _svc(TriageStub("chitchat"), reply, broker, mem, cache)
    tokens = await _tokens(svc, "哈喽")
    assert "".join(tokens) == "你好呀"
    assert broker.calls == []                                   # 不检索
    assert reply.calls[0]["mode"] == "chitchat" and reply.calls[0]["chunks"] == []
    assert reply.calls[0]["memory"] == {"profile_text": "reply_style: 古风", "recent": []}  # 偏好仍注入
    assert mem.appended == []                                   # 不写 query_history
    assert len(cache) == 0                                       # 不进缓存


async def test_transaction_runs_pipeline_but_not_cached():
    reply = Reply(["已为您提交"])
    broker = Broker({"kb_search": KB})
    mem = Mem()
    cache = AnswerCache()
    svc = _svc(TriageStub("transaction"), reply, broker, mem, cache)
    tokens = await _tokens(svc, "帮我下个订单")
    assert "".join(tokens) == "已为您提交"
    assert reply.calls and reply.calls[0]["mode"] is None       # 走正常答复链路
    assert mem.appended                                          # 写历史
    assert len(cache) == 0                                       # 交易结果不缓存


async def test_out_of_scope_fallback_no_downstream():
    reply = Reply(["不该出现"])
    broker = Broker()
    mem = Mem()
    svc = _svc(TriageStub("out_of_scope", is_service=False), reply, broker, mem, AnswerCache())
    tokens = await _tokens(svc, "帮我写首诗")
    assert tokens == [FALLBACK]
    assert reply.calls == [] and broker.calls == [] and mem.appended == []


async def test_knowledge_runs_pipeline_and_caches():
    reply = Reply(["据资料可部署"])
    broker = Broker({"kb_search": KB})
    mem = Mem()
    cache = AnswerCache()
    svc = _svc(TriageStub("knowledge"), reply, broker, mem, cache)
    tokens = await _tokens(svc, "怎么部署")
    assert "".join(tokens) == "据资料可部署"
    assert mem.appended                                          # 写历史
    assert len(cache) == 1                                       # 知识类进缓存
    assert cache.get("怎么部署", "u1", pref_sig="") == "据资料可部署"
