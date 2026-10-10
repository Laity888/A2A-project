"""工单 13：UI translate 透传工具调用 trace 的离线测（不连真服务）。"""

from __future__ import annotations

from types import SimpleNamespace

from a2a.helpers import new_data_artifact, new_text_message

from ui.translate import a2a_to_events


class _Chunk:
    """最小 StreamResponse 替身：只带一个含 trace DataPart 的 task artifact。"""

    def __init__(self, artifact):
        self.task = SimpleNamespace(artifacts=[artifact], status=SimpleNamespace(state=3))

    def HasField(self, name):  # noqa: N802
        return name == "task"


class _StatusChunk:
    """StreamResponse 替身：只带 status_update（含阶段文案 message）。"""

    def __init__(self, label, state=1):
        msg = new_text_message(label)
        self.status_update = SimpleNamespace(
            status=SimpleNamespace(state=state, message=msg)
        )

    def HasField(self, name):  # noqa: N802
        return name == "status_update"


def test_status_event_carries_phase_label_without_body():
    events = a2a_to_events(_StatusChunk("调用工具 kb_search…"))
    assert len(events) == 1
    assert events[0]["kind"] == "status"
    assert events[0]["message"] == "调用工具 kb_search…"
    assert not any(e["kind"] == "token" for e in events)  # 状态文案不得进正文


def test_trace_event_carries_tool_calls():
    trace = {
        "is_service": True, "intent": "return",
        "tool_calls": [{"round": 1, "name": "kb_search", "args_summary": '{"query":"退货"}', "result_summary": "命中1条"}],
        "retrieval": {"count": 1, "chunks": []},
        "timings": {"triage_ms": 1.0, "react_ms": 2.0, "reply_ms": 3.0},
    }
    art = new_data_artifact("response", trace)
    events = a2a_to_events(_Chunk(art))

    kinds = [e["kind"] for e in events]
    assert "status" in kinds
    trace_events = [e for e in events if e["kind"] == "trace"]
    assert trace_events, "应透传 trace 事件"
    assert trace_events[0]["trace"]["tool_calls"][0]["name"] == "kb_search"


def test_sources_event_from_data_part():
    data = {"sources": [{"source": "a", "source_url": "http://x/1", "category": "general", "score": 0.9}], "count": 1}
    art = new_data_artifact("sources", data)
    events = a2a_to_events(_Chunk(art))

    kinds = [e["kind"] for e in events]
    assert "sources" in kinds
    src = next(e for e in events if e["kind"] == "sources")
    assert src["data"]["count"] == 1 and src["data"]["sources"][0]["source_url"] == "http://x/1"
    assert not any(e["kind"] == "trace" for e in events)   # sources 不应被误判为 trace
