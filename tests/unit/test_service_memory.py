"""接待 service 接入长期记忆/偏好 + 偏好签名进缓存（离线）。

不连真 LLM/DB，用假 memory + 记录 memory 入参的假 reply + 真 AnswerCache，断言：
  ① 客服正常链路：profile 提前读(算签名)→ 结果透传给 reply_stream(memory=...)，成功后 append_history；
  ② 非客服兜底：读 profile(为签名)但不读最近咨询、不写历史；
  ③ 缓存命中：仍写历史；
  ④ 无 memory → reply 收到 memory=None（向后兼容）；
  ⑤ 回归：改偏好 → 签名变 → L1 不再命中旧风格答案、按新偏好重生成（修"缓存吞掉新偏好"）。
"""

from __future__ import annotations

from agents.reception.answer_cache import AnswerCache
from agents.reception.service import ReceptionService

FALLBACK = "抱歉，我目前只能处理客服/工单相关的问题。"
KB = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'


class Triage:
    def __init__(self, verdict): self.verdict = verdict
    async def classify(self, text): return dict(self.verdict)


class LLM:
    def __init__(self, responses=None): self._r = list(responses or [])
    async def chat_tools(self, messages, tools):
        return self._r.pop(0) if self._r else {"content": "", "tool_calls": []}


class Broker:
    def __init__(self, results): self.results = results; self.calls = []
    async def list_tools(self): return []
    async def call(self, name, args, context_id=None):
        self.calls.append((name, args)); return self.results[name]


class Reply:
    def __init__(self, deltas): self.deltas = list(deltas); self.memories = []
    async def reply_stream(self, query, chunks, history, context_id=None, memory=None):
        self.memories.append(memory)
        for d in self.deltas:
            yield d


class Session:
    def __init__(self, history=None): self._h = list(history or []); self.appended = []
    async def history(self, cid): return list(self._h)
    async def append(self, cid, u, a, trace=None): self.appended.append((u, a))


class Mem:
    """假记忆：get_profile/get_recent_history/append_history（同步，被 service 用 to_thread 调）。"""
    def __init__(self, profile=None, recent=None):
        self.profile = dict(profile or {})
        self.recent = list(recent or [])
        self.profile_calls = []
        self.appended = []

    def get_profile(self, user_id):
        self.profile_calls.append(user_id)
        return dict(self.profile)

    def get_recent_history(self, user_id, n=None):
        return list(self.recent)

    def append_history(self, user_id, intent, query, context_id=None):
        self.appended.append((user_id, intent, query, context_id))


async def _run(svc, text, cid="c1", uid="u1"):
    tokens = []
    async for ev in svc.stream(text, cid, user_id=uid):
        if ev["ev"] == "token":
            tokens.append(ev["text"])
    return tokens


def _svc(reply, mem, *, triage, cache=None):
    return ReceptionService(
        llm=LLM(),   # 模型不调工具 → 兜底强制补一次 kb_search
        triage=triage, tool_broker=Broker({"kb_search": KB}), reply_client=reply,
        session=Session(), agent_prompt="S", fallback_text=FALLBACK, max_steps=4,
        answer_cache=cache, memory=mem,
    )


async def test_service_injects_memory_and_records_history():
    mem = Mem(profile={"reply_style": "简洁"}, recent=[{"query": "如何部署"}])
    reply = Reply(["可为您办理退货"])
    svc = _svc(reply, mem, triage=Triage({"is_service": True, "intent": "return", "reason": "y"}))
    tokens = await _run(svc, "我要退货")
    assert "".join(tokens) == "可为您办理退货"
    assert mem.profile_calls == ["u1"]                              # 提前读了偏好
    assert reply.memories == [{"profile_text": "reply_style: 简洁", "recent": ["如何部署"]}]
    assert ("u1", "return", "我要退货", "c1") in mem.appended        # 成功后写历史


async def test_service_non_service_reads_profile_for_sig_but_no_history():
    mem = Mem(profile={"reply_style": "简洁"})
    reply = Reply(["不该出现"])
    svc = _svc(reply, mem, triage=Triage({"is_service": False, "intent": "chat", "reason": "闲聊"}))
    tokens = await _run(svc, "今天天气")
    assert tokens == [FALLBACK]
    assert mem.profile_calls == ["u1"]                              # 为算签名会读 profile
    assert mem.appended == []                                       # 非客服不写历史
    assert reply.memories == []                                     # 不走生成


async def test_service_cache_hit_still_records_history():
    cache = AnswerCache()
    cache.set("我要退货", "缓存答案", "u1")                          # 空偏好 → 签名 ""
    mem = Mem(profile={})                                           # 无偏好 → pref_sig="" → 命中
    reply = Reply(["不该出现"])
    svc = _svc(reply, mem, triage=Triage({"is_service": True, "intent": "x", "reason": "y"}), cache=cache)
    tokens = await _run(svc, "我要退货")
    assert tokens == ["缓存答案"]
    assert reply.memories == []                                     # 命中不走生成
    assert ("u1", "cache", "我要退货", "c1") in mem.appended          # 命中也写历史


async def test_service_without_memory_backward_compatible():
    reply = Reply(["答复"])
    svc = _svc(reply, None, triage=Triage({"is_service": True, "intent": "x", "reason": "y"}))
    tokens = await _run(svc, "问题")
    assert "".join(tokens) == "答复"
    assert reply.memories == [None]                                 # 无记忆 → memory=None


async def test_cache_respects_preference_change():
    # 回归"缓存吞掉新偏好"：改风格后第二次同问不应命中旧风格缓存，应按新风格重生成
    cache = AnswerCache()
    triage = Triage({"is_service": True, "intent": "greet", "reason": "y"})
    mem = Mem(profile={"reply_style": "古风"})
    reply = Reply(["古风答复"])
    svc = _svc(reply, mem, triage=triage, cache=cache)

    t1 = await _run(svc, "哈喽")
    assert "".join(t1) == "古风答复"
    assert len(reply.memories) == 1

    mem.profile = {"reply_style": "科技风"}                          # 改偏好 → 签名变
    reply.deltas = ["科技答复"]
    t2 = await _run(svc, "哈喽")
    assert "".join(t2) == "科技答复"                                # 新风格生效，没被旧缓存短路
    assert len(reply.memories) == 2                                 # 第二次确实重跑了生成
