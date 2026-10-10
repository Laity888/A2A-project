"""答复 Agent 提示词注入长期记忆/偏好（离线）。

验证 build_reply_messages 把 payload['memory'] 渲染成独立块拼进 system，
无 memory 时 system 原样不变（向后兼容既有 reply 契约测试）。
"""

from __future__ import annotations

from agents.reply.agent import _format_memory_block, build_reply_messages


def test_format_memory_block_renders_both():
    blk = _format_memory_block({"profile_text": "a: b", "recent": ["q1", "q2"]})
    assert "【用户偏好】a: b" in blk
    assert "【最近咨询】q1 / q2" in blk


def test_format_memory_block_empty():
    assert _format_memory_block(None) == ""
    assert _format_memory_block({}) == ""
    assert _format_memory_block({"profile_text": "", "recent": []}) == ""


def test_memory_injected_into_system_prompt():
    msgs = build_reply_messages(
        {"query": "怎么部署", "chunks": [], "history": [],
         "memory": {"profile_text": "风格: 简洁", "recent": ["支持什么格式"]}},
        "SYS",
    )
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"].startswith("SYS")
    assert "【用户偏好】风格: 简洁" in msgs[0]["content"]
    assert "【最近咨询】支持什么格式" in msgs[0]["content"]


def test_no_memory_system_unchanged():
    msgs = build_reply_messages({"query": "x", "chunks": [], "history": []}, "SYS")
    assert msgs[0]["content"] == "SYS"
