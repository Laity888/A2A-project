"""接待 service 流式事件源单测（工单：流式 + 每轮状态）。

不连真 LLM/MCP/网络，直接消费 ReceptionService.stream()，断言：
  ① 分阶段 status 事件（triage → tool → answer）逐轮透出，供 UI 状态胶囊；
  ② 答复按多段 token 增量产出（真流式的证据）；
  ③ trace 事件带工具调用与检索命中；
  ④ 非客服只兜底、不进 ReAct/答复；⑤ 下游失败仍产出一段兜底 token + error trace。
"""

from __future__ import annotations

from agents.reception.answer_cache import AnswerCache
from agents.reception.service import ReceptionService

FALLBACK = "抱歉，我目前只能处理客服/工单相关的问题。"


class Triage:
    def __init__(self, verdict): self.verdict = verdict
    async def classify(self, text): return dict(self.verdict)


class LLM:
    def __init__(self, responses): self._r = list(responses)
    async def chat_tools(self, messages, tools):
        return self._r.pop(0) if self._r else {"content": "", "tool_calls": []}


class Broker:
    def __init__(self, results): self.results = results; self.tools = []; self.calls = []
    async def list_tools(self): return self.tools
    async def call(self, name, args, context_id=None):
        self.calls.append((name, args)); return self.results[name]


class MultiReply:
    """答复流式：产出多段增量，证明 service 逐段透出而非一次性。"""
    def __init__(self, deltas, raise_it=False):
        self.deltas = deltas; self.raise_it = raise_it; self.calls = []
    async def reply_stream(self, query, chunks, history, context_id=None, memory=None):
        self.calls.append((query, chunks, history))
        if self.raise_it:
            raise RuntimeError("reply down")
        for d in self.deltas:
            yield d


class Session:
    def __init__(self, history=None): self._h = list(history or []); self.appended = []
    async def history(self, cid): return list(self._h)
    async def append(self, cid, u, a, trace=None): self.appended.append((u, a))


def _svc(triage, llm, broker, reply, session):
    return ReceptionService(
        llm=llm, triage=triage, tool_broker=broker, reply_client=reply, session=session,
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4,
    )


async def _collect(service, text="我要退货", cid="c1"):
    phases, tokens, traces = [], [], []
    async for ev in service.stream(text, cid):
        if ev["ev"] == "status":
            phases.append(ev.get("phase"))
        elif ev["ev"] == "token":
            tokens.append(ev["text"])
        elif ev["ev"] == "trace":
            traces.append(ev["data"])
    return phases, tokens, traces


async def test_service_stream_emits_phases_and_incremental_tokens():
    kb = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'
    triage = Triage({"is_service": True, "intent": "return", "reason": "退货"})
    llm = LLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "我要退货"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    broker = Broker({"kb_search": kb})
    reply = MultiReply(["可为您", "办理退货"])
    session = Session()
    phases, tokens, traces = await _collect(_svc(triage, llm, broker, reply, session))

    # 分阶段状态：分流 → 工具 → 生成答复
    assert "triage" in phases and "tool" in phases and "answer" in phases
    # 答复确为多段增量（真流式），拼接后完整
    assert len(tokens) == 2 and "".join(tokens) == "可为您办理退货"
    # trace 带工具调用与检索命中
    tr = traces[-1]
    assert tr["tool_calls"][0]["name"] == "kb_search"
    assert tr["retrieval"]["count"] == 1
    # trace 带 ReAct 耗时拆分（决策/检索/次数）
    assert tr["timings"]["n_tool_calls"] == 1
    assert "react_llm_ms" in tr["timings"] and "react_tool_ms" in tr["timings"]
    # 会话已追加完整答复
    assert session.appended and session.appended[0][1] == "可为您办理退货"


async def test_service_stream_non_service_only_fallback():
    triage = Triage({"is_service": False, "intent": "chat", "reason": "闲聊"})
    reply = MultiReply(["不该出现"])
    broker = Broker({})
    phases, tokens, traces = await _collect(_svc(triage, LLM([]), broker, reply, Session()), "今天天气")

    assert tokens == [FALLBACK]
    assert phases == ["triage"]                     # 只到分流，不进入 ReAct/答复
    assert broker.calls == [] and reply.calls == []  # 不调工具、不调答复
    assert traces[-1]["is_service"] is False


async def test_service_stream_reply_failure_degrades():
    triage = Triage({"is_service": True, "intent": "x", "reason": "y"})
    reply = MultiReply([], raise_it=True)
    phases, tokens, traces = await _collect(_svc(triage, LLM([{"content": "ok", "tool_calls": []}]), Broker({}), reply, Session()))
    assert tokens == [FALLBACK]
    assert traces[-1].get("error")


async def test_service_stream_grounding_guard_when_no_tool_called():
    # 复现：客服问题但模型一次工具都没调（被历史旧答案带偏）→ 兜底强制补一次 kb_search
    triage = Triage({"is_service": True, "intent": "x", "reason": "y"})
    llm = LLM([{"content": "我已知答案", "tool_calls": []}])   # 模型不调工具
    kb = '{"count":1,"chunks":[{"text":"支持PDF","source_url":"http://x/1","category":"general","score":0.9}]}'
    broker = Broker({"kb_search": kb})
    reply = MultiReply(["据资料支持PDF"])
    phases, tokens, traces = await _collect(_svc(triage, llm, broker, reply, Session()))

    assert broker.calls == [("kb_search", {"query": "我要退货"})]        # 兜底检索生效
    assert reply.calls and reply.calls[0][1][0]["source_url"] == "http://x/1"  # chunks 进了答复
    assert traces[-1]["retrieval"]["count"] == 1
    assert traces[-1]["timings"]["n_tool_calls"] == 1


async def test_service_stream_answer_cache_skips_pipeline():
    cache = AnswerCache()
    triage = Triage({"is_service": True, "intent": "return", "reason": "退货"})
    kb = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'
    llm = LLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "我要退货政策"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    broker = Broker({"kb_search": kb})
    reply = MultiReply(["可为您办理退货"])
    svc = ReceptionService(
        llm=llm, triage=triage, tool_broker=broker, reply_client=reply, session=Session(),
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4, answer_cache=cache,
    )

    async for _ in svc.stream("我要退货政策", "c1"):
        pass
    n_broker, n_reply = len(broker.calls), len(reply.calls)
    assert n_broker == 1 and n_reply == 1

    # 第二次相同问题：命中缓存，不再跑检索/生成
    tokens2, traces2 = [], []
    async for ev in svc.stream("我要退货政策", "c1"):
        if ev["ev"] == "token":
            tokens2.append(ev["text"])
        elif ev["ev"] == "trace":
            traces2.append(ev["data"])
    assert len(broker.calls) == n_broker and len(reply.calls) == n_reply  # 未再触发
    assert "".join(tokens2) == "可为您办理退货"
    assert traces2[-1].get("cache_hit") is True

    # 归一化：空格/标点不同的同义问法也应命中
    tokens3 = []
    async for ev in svc.stream("我要退货 政策 ？", "c1"):
        if ev["ev"] == "token":
            tokens3.append(ev["text"])
    assert "".join(tokens3) == "可为您办理退货"
    assert len(broker.calls) == n_broker  # 仍未触发检索


async def test_service_stream_answer_cache_serves_within_conversation():
    # 放宽后：带历史（同会话）也应命中缓存、跳过链路
    cache = AnswerCache()
    cache.set("我要退货政策", "可为您办理退货")   # 模拟之前独立提问时存下的
    triage = Triage({"is_service": True, "intent": "return", "reason": "退货"})
    llm = LLM([{"content": "不该走到这", "tool_calls": []}])
    broker = Broker({})
    reply = MultiReply(["不该走到这"])
    session = Session(history=[{"role": "user", "text": "早"}, {"role": "assistant", "text": "您好"}])  # 非空历史
    svc = ReceptionService(
        llm=llm, triage=triage, tool_broker=broker, reply_client=reply, session=session,
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4, answer_cache=cache,
    )
    tokens, traces = [], []
    async for ev in svc.stream("我要退货政策", "c1"):
        if ev["ev"] == "token":
            tokens.append(ev["text"])
        elif ev["ev"] == "trace":
            traces.append(ev["data"])
    assert "".join(tokens) == "可为您办理退货"
    assert traces[-1].get("cache_hit") is True
    assert broker.calls == [] and reply.calls == []   # 带历史也命中，不跑检索/生成


class KeyedSession:
    """按 key 存取历史，用于断言会话按 user_id 命名空间隔离；并记录 trace。"""
    def __init__(self): self._h = {}; self.appended = []; self.traces = []
    async def history(self, key): return list(self._h.get(key, []))
    async def append(self, key, u, a, trace=None):
        self._h.setdefault(key, []).extend([{"role": "user", "text": u}, {"role": "assistant", "text": a}])
        self.appended.append((key, u, a))
        self.traces.append((key, trace))


def _kb_broker():
    kb = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'
    return Broker({"kb_search": kb})


async def test_service_stream_cache_isolated_per_user():
    # 同一独立问题：u1 跑完整链路存缓存 → u2 不得命中（各跑各的），u1 再问才命中
    cache = AnswerCache()  # scope=user
    triage = Triage({"is_service": True, "intent": "return", "reason": "退货"})
    broker = _kb_broker()
    reply = MultiReply(["可为您办理退货"])
    svc = ReceptionService(
        llm=LLM([{"content": "", "tool_calls": []}]),  # 兜底会强制补一次 kb_search
        triage=triage, tool_broker=broker, reply_client=reply, session=KeyedSession(),
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4, answer_cache=cache,
    )

    async for _ in svc.stream("怎么退货", "c1", user_id="u1"):
        pass
    assert len(broker.calls) == 1 and len(reply.calls) == 1     # u1 首次跑链路

    async for _ in svc.stream("怎么退货", "c1", user_id="u2"):
        pass
    assert len(broker.calls) == 2 and len(reply.calls) == 2     # u2 没命中，又跑一次（隔离）

    async for _ in svc.stream("怎么退货", "c1", user_id="u1"):
        pass
    assert len(broker.calls) == 2 and len(reply.calls) == 2     # u1 再次 → 命中缓存，不跑链路


async def test_service_stream_session_isolated_per_user():
    cache = AnswerCache(enabled=False)  # 关缓存，专测会话隔离
    triage = Triage({"is_service": True, "intent": "return", "reason": "退货"})
    broker = _kb_broker()
    sess = KeyedSession()
    svc = ReceptionService(
        llm=LLM([{"content": "", "tool_calls": []}]),
        triage=triage, tool_broker=broker, reply_client=MultiReply(["答复"]), session=sess,
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4, answer_cache=cache,
    )
    async for _ in svc.stream("问题一", "same-ctx", user_id="u1"):
        pass
    async for _ in svc.stream("问题二", "same-ctx", user_id="u2"):
        pass
    keys = [k for k, _u, _a in sess.appended]
    assert "u1:same-ctx" in keys and "u2:same-ctx" in keys     # 同 context_id 不同用户 → 不同命名空间
    assert await sess.history("u1:same-ctx") and await sess.history("u2:same-ctx")
    assert await sess.history("u3:same-ctx") == []             # 别的用户看不到


class FakeScache:
    """语义缓存(L2)桩：可控命中/抛错，记录读与写。"""
    def __init__(self, hit=None, raise_lookup=False, raise_record=False):
        self.hit = hit; self.raise_lookup = raise_lookup; self.raise_record = raise_record
        self.lookup_calls = []; self.records = []

    async def lookup(self, text, user_id, pref_sig=""):
        self.lookup_calls.append((text, user_id))
        if self.raise_lookup:
            raise RuntimeError("sc down")
        return self.hit

    async def record(self, text, answer, user_id, pref_sig=""):
        self.records.append((text, answer, user_id))
        if self.raise_record:
            raise RuntimeError("sc down")


def _svc2(triage, llm, broker, reply, session, *, cache, scache):
    return ReceptionService(
        llm=llm, triage=triage, tool_broker=broker, reply_client=reply, session=session,
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4,
        answer_cache=cache, semantic_cache=scache,
    )


async def _run2(svc, text="怎么查配置", cid="c1", uid="u1"):
    tokens, traces = [], []
    async for ev in svc.stream(text, cid, user_id=uid):
        if ev["ev"] == "token":
            tokens.append(ev["text"])
        elif ev["ev"] == "trace":
            traces.append(ev["data"])
    return tokens, traces


async def test_semantic_cache_hit_shortcircuits_and_backfills_l1():
    cache = AnswerCache()  # L1 空
    sc = FakeScache(hit="语义缓存答案")
    broker = Broker({})
    reply = MultiReply(["不该出现"])
    svc = _svc2(Triage({"is_service": True, "intent": "cfg", "reason": "y"}),
                LLM([]), broker, reply, Session(), cache=cache, scache=sc)
    tokens, traces = await _run2(svc)
    assert tokens == ["语义缓存答案"]
    assert broker.calls == [] and reply.calls == []          # 跳过 ReAct/检索/生成
    assert traces[-1]["cache_hit"] is True and traces[-1]["cache_level"] == "semantic"
    assert cache.get("怎么查配置", "u1") == "语义缓存答案"       # 回填 L1


async def test_semantic_cache_miss_runs_pipeline_and_records():
    sc = FakeScache(hit=None)
    broker = _kb_broker()
    reply = MultiReply(["据资料答复"])
    svc = _svc2(Triage({"is_service": True, "intent": "x", "reason": "y"}),
                LLM([{"content": "", "tool_calls": []}]), broker, reply, Session(),
                cache=AnswerCache(), scache=sc)
    tokens, traces = await _run2(svc, text="怎么部署", uid="u2")
    assert "".join(tokens) == "据资料答复"
    assert sc.lookup_calls == [("怎么部署", "u2")]             # 透传 user_id
    assert sc.records == [("怎么部署", "据资料答复", "u2")]      # 独立问题写回 L2
    assert traces[-1].get("cache_hit") is not True


async def test_semantic_cache_lookup_error_degrades_to_miss():
    sc = FakeScache(raise_lookup=True)
    broker = _kb_broker()
    reply = MultiReply(["正常答复"])
    svc = _svc2(Triage({"is_service": True, "intent": "x", "reason": "y"}),
                LLM([{"content": "", "tool_calls": []}]), broker, reply, Session(),
                cache=AnswerCache(), scache=sc)
    tokens, _ = await _run2(svc)
    assert "".join(tokens) == "正常答复"                        # 未崩，走正常链路


async def test_semantic_cache_record_error_is_swallowed():
    sc = FakeScache(hit=None, raise_record=True)
    broker = _kb_broker()
    reply = MultiReply(["答复内容"])
    svc = _svc2(Triage({"is_service": True, "intent": "x", "reason": "y"}),
                LLM([{"content": "", "tool_calls": []}]), broker, reply, Session(),
                cache=AnswerCache(), scache=sc)
    tokens, traces = await _run2(svc)
    assert "".join(tokens) == "答复内容"                         # 写失败不影响返回
    assert traces[-1].get("error") is None                       # 非兜底


# ---------- 回归：兜底/缓存命中回合也必须落会话历史（否则刷新/换端读回为空）----------

async def test_non_service_fallback_is_persisted_to_session():
    sess = KeyedSession()
    svc = _svc2(Triage({"is_service": False, "intent": "chat", "reason": "闲聊"}),
                LLM([]), Broker({}), MultiReply(["不该出现"]), sess,
                cache=AnswerCache(), scache=None)
    tokens, _ = await _run2(svc, text="hello")
    assert tokens == [FALLBACK]
    assert sess.appended == [("u1:c1", "hello", FALLBACK)]        # 兜底回合也写历史


async def test_answer_cache_hit_is_persisted_to_session():
    cache = AnswerCache()
    cache.set("怎么退货", "可为您办理退货", "u1")                  # 预置（同用户独立提问）
    sess = KeyedSession()
    svc = _svc2(Triage({"is_service": True, "intent": "x", "reason": "y"}),
                LLM([{"content": "", "tool_calls": []}]), Broker({}), MultiReply(["不该出现"]), sess,
                cache=cache, scache=None)
    tokens, _ = await _run2(svc, text="怎么退货")
    assert tokens == ["可为您办理退货"]
    assert sess.appended == [("u1:c1", "怎么退货", "可为您办理退货")]   # 命中缓存也写历史


async def test_error_fallback_is_persisted_to_session():
    sess = KeyedSession()
    svc = _svc2(Triage({"is_service": True, "intent": "x", "reason": "y"}),
                LLM([{"content": "", "tool_calls": []}]), _kb_broker(),
                MultiReply([], raise_it=True), sess, cache=AnswerCache(), scache=None)
    await _run2(svc, text="查订单")
    assert sess.appended and sess.appended[-1][2] == FALLBACK      # 异常兜底也写历史


async def test_success_path_persists_full_trace_to_session():
    sess = KeyedSession()
    kb = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'
    llm = LLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "我要退货"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    svc = ReceptionService(
        llm=llm, triage=Triage({"is_service": True, "intent": "return", "reason": "退货"}),
        tool_broker=Broker({"kb_search": kb}), reply_client=MultiReply(["可为您办理退货"]), session=sess,
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4,
    )
    await _collect(svc, "我要退货", "c1")
    key, trace = sess.traces[-1]
    assert key == "c1"
    assert trace["tool_calls"][0]["name"] == "kb_search"           # 工具调用落库
    assert trace["retrieval"]["count"] == 1                        # 检索命中落库


async def test_service_stream_emits_sources_before_answer():
    # 体感提速①：拿到 chunks 后、答复生成前，先发 sources 事件（来源早现）
    kb = ('{"count":2,"chunks":['
          '{"text":"a","source_url":"http://x/1","category":"general","score":0.9},'
          '{"text":"b","source":"y","source_url":"http://x/2"}]}')
    triage = Triage({"is_service": True, "intent": "cfg", "reason": "y"})
    llm = LLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "q"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    reply = MultiReply(["答复"])
    evs = []
    async for ev in _svc(triage, llm, Broker({"kb_search": kb}), reply, Session()).stream("q", "c1"):
        evs.append(ev)

    src = next((e for e in evs if e["ev"] == "sources"), None)
    assert src and src["data"]["count"] == 2
    assert src["data"]["sources"][0]["source_url"] == "http://x/1"
    # sources 不得夹正文文本（只是来源），且应早于答复 token
    idx_src = next(i for i, e in enumerate(evs) if e["ev"] == "sources")
    idx_tok = next(i for i, e in enumerate(evs) if e["ev"] == "token")
    assert idx_src < idx_tok
