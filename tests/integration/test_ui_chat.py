"""Web UI BFF 契约测试（工单 06）—— 注入 fake 接待流提供器，进程内驱动，不连真接待/网络。

对齐票 07：POST /api/chat 把接待的 A2A 流翻译成浏览器 SSE 事件 token/status/trace/done；
GET / 返回前端页。SSE 格式化 + 事件翻译做成纯函数便于离线断言。
"""

from __future__ import annotations

import json

import httpx
import pytest

from ui.app import create_app
from ui.auth import AuthContext
from ui.sse import format_sse


def _noauth():
    """关闭登录：这些用例只验 SSE 翻译/会话 cookie，不涉及鉴权。"""
    return AuthContext(enabled=False, store=None, secret="", cookie_name="a2a_cs_uid", token_ttl_seconds=0)


def test_format_sse_shape():
    out = format_sse("token", {"text": "hi"})
    assert out.startswith("event: token\ndata: ")
    assert out.endswith("\n\n")
    assert json.loads(out.split("data: ", 1)[1].strip()) == {"text": "hi"}


def _fake_provider_factory(events):
    def factory(text, context_id, user_id=None):
        async def gen():
            for ev in events:
                yield ev
        return gen()
    return factory


async def _post_chat(app, message="你好"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as hc:
        r = await hc.post("/api/chat", json={"message": message})
    return r


async def test_chat_emits_translated_sse_events():
    events = [
        {"kind": "status", "state": "working"},
        {"kind": "token", "text": "你好，"},
        {"kind": "token", "text": "可以帮您"},
        {"kind": "trace", "trace": {"intent": "greet", "is_service": True}},
        {"kind": "done"},
    ]
    app = create_app(provider_factory=_fake_provider_factory(events), auth=_noauth())
    r = await _post_chat(app)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert "event: status" in body
    assert "event: token" in body
    assert "你好，" in body and "可以帮您" in body
    assert "event: trace" in body and '"intent"' in body
    assert "event: done" in body


async def test_chat_sets_session_cookie_when_absent():
    app = create_app(provider_factory=_fake_provider_factory([{"kind": "done"}]), auth=_noauth())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as hc:
        r = await hc.post("/api/chat", json={"message": "hi"})
    assert "set-cookie" in {k.lower() for k in r.headers.keys()}


async def test_error_event_emitted_on_provider_failure():
    def factory(text, context_id, user_id=None):
        async def gen():
            yield {"kind": "status", "state": "working"}
            raise RuntimeError("reception down")
            yield  # noqa
        return gen()
    app = create_app(provider_factory=factory, auth=_noauth())
    r = await _post_chat(app)
    assert "event: error" in r.text


async def test_index_serves_frontend():
    app = create_app(provider_factory=_fake_provider_factory([{"kind": "done"}]), auth=_noauth())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as hc:
        r = await hc.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()
