"""工单 12-A：ReActAgent 工具循环离线单测（fake LLM + fake ToolBroker，不碰网络/MCP/qwen）。"""

from __future__ import annotations

from agents.reception.react import ReActAgent


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def chat_tools(self, messages, tools):
        self.calls.append((messages, tools))
        return self._responses.pop(0)


class FakeBroker:
    def __init__(self, tools, results):
        self._tools = tools
        self._results = results
        self.calls = []

    async def list_tools(self):
        return self._tools

    async def call(self, name, args, context_id=None):
        self.calls.append((name, args))
        return self._results[name]


TOOLS = [{"type": "function", "function": {"name": "kb_search", "description": "d", "parameters": {}}}]
KB_RESULT = '{"count":1,"chunks":[{"text":"7天可退","source_url":"http://x/1"}]}'


def _agent(llm, broker, max_steps=4):
    return ReActAgent(llm=llm, broker=broker, system_prompt="S", max_steps=max_steps)


async def test_single_tool_then_final():
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "退货"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    broker = FakeBroker(TOOLS, {"kb_search": KB_RESULT})
    out = await _agent(llm, broker).run("退货政策", [])
    assert broker.calls == [("kb_search", {"query": "退货"})]
    assert len(out["evidence"]) == 1
    assert out["evidence"][0]["name"] == "kb_search"


async def test_parallel_tool_calls_all_executed():
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "a", "name": "kb_search", "args": {"query": "q1"}},
            {"id": "b", "name": "kb_search", "args": {"query": "q2"}},
        ]},
        {"content": "done", "tool_calls": []},
    ])
    broker = FakeBroker(TOOLS, {"kb_search": KB_RESULT})
    out = await _agent(llm, broker).run("x", [])
    assert len(broker.calls) == 2
    assert len(out["evidence"]) == 2


async def test_respects_max_steps():
    # LLM 永远要调工具 → 循环应在 max_steps 处停
    resp = {"content": "", "tool_calls": [{"id": "z", "name": "kb_search", "args": {"query": "loop"}}]}
    llm = ScriptedLLM([dict(resp) for _ in range(10)])
    broker = FakeBroker(TOOLS, {"kb_search": KB_RESULT})
    await _agent(llm, broker, max_steps=2).run("x", [])
    assert len(llm.calls) == 2   # 只跑 2 轮
    assert len(broker.calls) == 2


async def test_no_tool_returns_empty_evidence():
    llm = ScriptedLLM([{"content": "直接答", "tool_calls": []}])
    broker = FakeBroker(TOOLS, {})
    out = await _agent(llm, broker).run("闲聊", [])
    assert out["evidence"] == []
    assert broker.calls == []


async def test_history_included_in_messages():
    llm = ScriptedLLM([{"content": "ok", "tool_calls": []}])
    broker = FakeBroker(TOOLS, {})
    hist = [{"role": "user", "text": "上一句"}, {"role": "assistant", "text": "回过"}]
    await _agent(llm, broker).run("新问题", hist)
    msgs = llm.calls[0][0]
    assert msgs[0] == {"role": "system", "content": "S"}
    assert {"role": "user", "content": "上一句"} in msgs
    assert msgs[-1] == {"role": "user", "content": "新问题"}


async def test_run_stream_emits_tool_start_and_done_events():
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "退货"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    broker = FakeBroker(TOOLS, {"kb_search": KB_RESULT})
    events = [ev async for ev in _agent(llm, broker).run_stream("退货政策", [])]

    kinds = [ev["type"] for ev in events]
    assert "tool_start" in kinds and "tool_done" in kinds and "result" in kinds
    start = next(e for e in events if e["type"] == "tool_start")
    assert start["name"] == "kb_search" and start["round"] == 1
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["result"] == KB_RESULT and done["round"] == 1
    result = next(e for e in events if e["type"] == "result")
    assert len(result["evidence"]) == 1


async def test_max_tool_calls_caps_execution():
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "a", "name": "kb_search", "args": {"query": "q1"}},
            {"id": "b", "name": "kb_search", "args": {"query": "q2"}},
        ]},
        {"content": "", "tool_calls": [{"id": "c", "name": "kb_search", "args": {"query": "q3"}}]},
    ])
    broker = FakeBroker(TOOLS, {"kb_search": KB_RESULT})
    agent = ReActAgent(llm=llm, broker=broker, system_prompt="S", max_steps=4, max_tool_calls=2)
    out = await agent.run("x", [])
    assert len(broker.calls) == 2          # 硬上限：只执行 2 次
    assert len(out["evidence"]) == 2


async def test_run_stream_result_carries_timing_breakdown():
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "退货"}}]},
        {"content": "够了", "tool_calls": []},
    ])
    broker = FakeBroker(TOOLS, {"kb_search": KB_RESULT})
    events = [ev async for ev in _agent(llm, broker).run_stream("退货", [])]
    result = next(e for e in events if e["type"] == "result")
    n_done = sum(1 for e in events if e["type"] == "tool_done")
    assert result["n_calls"] == n_done == 1
    assert isinstance(result["llm_ms"], (int, float))
    assert isinstance(result["tool_ms"], (int, float))
