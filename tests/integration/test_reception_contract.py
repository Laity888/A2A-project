"""接待 Agent 契约测试（工单 05 起，工单 12 升级为 ReAct + MCP 工具）。

注入 fake triage/llm/tool_broker/reply/session，进程内驱动，不碰真 LLM/MCP/网络。
覆盖：① 非客服→兜底、不进 ReAct/答复；② 客服→ReAct 调 kb_search→答复 Agent 合成、trace 含 tool_calls；
③ 答复失败→兜底 COMPLETED；④ 工具失败→兜底 COMPLETED。
"""

from __future__ import annotations

import dataclasses

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

from agents.reception.executor import ReceptionAgentExecutor
from agents.reception.service import ReceptionService
from common import a2a_app
from common.config import load_agent

TEST_BASE = "http://testserver/"
COMPLETED = int(TaskState.TASK_STATE_COMPLETED)
FALLBACK = "抱歉，我目前只能处理客服/工单相关的问题。"


class FakeTriage:
    def __init__(self, verdict): self.verdict = dict(verdict); self.calls = []
    async def classify(self, text): self.calls.append(text); return dict(self.verdict)


class ScriptedLLM:
    def __init__(self, responses): self._r = list(responses); self.calls = []
    async def chat_tools(self, messages, tools):
        self.calls.append((messages, tools))
        return self._r.pop(0) if self._r else {"content": "", "tool_calls": []}


class FakeBroker:
    def __init__(self, results, tools=None): self.results = results; self.tools = tools or []; self.calls = []
    async def list_tools(self): return self.tools
    async def call(self, name, args, context_id=None):
        self.calls.append((name, args))
        r = self.results[name]
        if isinstance(r, Exception): raise r
        return r


class FakeReply:
    def __init__(self, answer="已为您处理", raise_it=False):
        self.answer = answer; self.raise_it = raise_it; self.calls = []
    async def reply(self, query, chunks, history, context_id=None):
        self.calls.append((query, chunks, history))
        if self.raise_it: raise RuntimeError("reply down")
        return self.answer
    async def reply_stream(self, query, chunks, history, context_id=None, memory=None):
        self.calls.append((query, chunks, history))
        if self.raise_it: raise RuntimeError("reply down")
        yield self.answer


class FakeSession:
    def __init__(self, history=None): self._h = list(history or []); self.appended = []
    async def history(self, cid): return list(self._h)
    async def append(self, cid, u, a, trace=None): self.appended.append((u, a))


def _make_app(*, triage, llm, broker, reply, session):
    cfg = load_agent("reception")
    card_cfg = dataclasses.replace(cfg.card, public_url=TEST_BASE)
    service = ReceptionService(
        llm=llm, triage=triage, tool_broker=broker, reply_client=reply, session=session,
        agent_prompt="S", fallback_text=FALLBACK, max_steps=4,
    )
    app = a2a_app.build_app(ReceptionAgentExecutor(service), card_cfg)
    return cfg, app, a2a_app.to_agent_card(card_cfg)


async def _drive(app, card, text):
    texts, states, datas = [], [], []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as hc:
        client = await create_client(agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc))
        try:
            req = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))
            async for chunk in client.send_message(req):
                piece = get_stream_response_text(chunk)
                if piece: texts.append(piece)
                if chunk.HasField("task"):
                    states.append(int(chunk.task.status.state))
                    for art in chunk.task.artifacts:
                        for d in get_data_parts(list(art.parts)):
                            if isinstance(d, dict): datas.append(d)
                elif chunk.HasField("status_update"):
                    states.append(int(chunk.status_update.status.state))
        finally:
            await client.close()
    return "".join(texts), states, datas


async def test_non_service_fallback_no_tools_no_reply():
    triage = FakeTriage({"is_service": False, "intent": "chat", "reason": "闲聊"})
    broker = FakeBroker({}); reply = FakeReply()
    _cfg, app, card = _make_app(triage=triage, llm=ScriptedLLM([]), broker=broker, reply=reply, session=FakeSession())
    text, states, _ = await _drive(app, card, "今天天气不错")
    assert states[-1] == COMPLETED
    assert broker.calls == [] and reply.calls == []
    assert FALLBACK in text


async def test_service_react_calls_tool_then_reply():
    triage = FakeTriage({"is_service": True, "intent": "return", "reason": "退货"})
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "我要退货"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    kb = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1","category":"general","score":0.9}]}'
    broker = FakeBroker({"kb_search": kb})
    reply = FakeReply(answer="可为您办理退货")
    _cfg, app, card = _make_app(triage=triage, llm=llm, broker=broker, reply=reply, session=FakeSession())
    text, states, datas = await _drive(app, card, "我要退货")

    assert broker.calls == [("kb_search", {"query": "我要退货"})]
    assert reply.calls and reply.calls[0][1][0]["source_url"] == "http://x/1"  # chunks 进答复
    assert "可为您办理退货" in text
    assert states[-1] == COMPLETED
    trace = next((d for d in datas if "tool_calls" in d), None)
    assert trace and trace["tool_calls"][0]["name"] == "kb_search"


async def test_reply_failure_degrades_to_fallback():
    triage = FakeTriage({"is_service": True, "intent": "x", "reason": "y"})
    llm = ScriptedLLM([{"content": "ok", "tool_calls": []}])
    reply = FakeReply(raise_it=True)
    _cfg, app, card = _make_app(triage=triage, llm=llm, broker=FakeBroker({}), reply=reply, session=FakeSession())
    text, states, _ = await _drive(app, card, "查订单")
    assert states[-1] == COMPLETED
    assert FALLBACK in text


async def test_tool_failure_degrades_to_fallback():
    triage = FakeTriage({"is_service": True, "intent": "x", "reason": "y"})
    llm = ScriptedLLM([{"content": "", "tool_calls": [{"id": "c", "name": "kb_search", "args": {"query": "q"}}]}])
    broker = FakeBroker({"kb_search": RuntimeError("mcp down")})
    _cfg, app, card = _make_app(triage=triage, llm=llm, broker=broker, reply=FakeReply(), session=FakeSession())
    text, states, _ = await _drive(app, card, "查资料")
    assert states[-1] == COMPLETED
    assert FALLBACK in text
