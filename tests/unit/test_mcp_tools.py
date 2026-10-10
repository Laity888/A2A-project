"""工单 11：MCP 工具离线单测（直接调工具函数 + 断言 server 注册，不起 stdio 子进程）。"""

from __future__ import annotations

import inspect

import pytest

from mcp_server import tools


async def _fake_retrieval(query: str, context_id: str = ""):
    return {"count": 2, "chunks": [
        {"text": "支持7天退货", "source": "return", "source_url": "http://x/1", "category": "general", "score": 0.9},
        {"text": "联系客服", "source": "contact", "source_url": "http://x/2", "category": "general", "score": 0.5},
    ]}


async def test_kb_search_delegates_to_retrieval():
    result = await tools.kb_search("退货政策", retrieval=_fake_retrieval)
    assert result["count"] == 2
    assert result["chunks"][0]["source_url"] == "http://x/1"


def test_create_ticket_returns_structured_mock():
    r = tools.create_ticket(issue="杯子碎了要退货", contact="138")
    assert r["status"] == "created"
    assert r["ticket_id"]
    assert "杯子" in r["summary"]


def test_get_order_status_returns_structured_mock():
    r = tools.get_order_status("ORD123")
    assert r["order_id"] == "ORD123"
    assert "status" in r


def test_escalate_human_returns_handoff():
    r = tools.escalate_human(reason="投诉")
    assert r["escalated"] is True


def test_server_registers_all_tools():
    from mcp_server.server import mcp

    names = {t.name for t in mcp._tool_manager.list_tools()}
    for expected in ("kb_search", "create_ticket", "get_order_status", "escalate_human"):
        assert expected in names, f"未注册工具 {expected}（实际 {names}）"
