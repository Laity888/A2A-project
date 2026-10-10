"""会话端点契约测试（离线）：注入 auth + 假 conversation_store，进程内驱动 UI BFF，不连真接待/DB。

覆盖：未登录 401；/api/chat 触发会话 upsert 且透传 user_id；list/rename/delete/messages；
越权 rename→404；未注入 store 时 list 返回空、rename→501。
"""

from __future__ import annotations

import httpx

from ui.app import create_app
from ui.auth import AuthContext, UserStore


class FakeConvStore:
    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.messages: dict[tuple[str, str], list[dict]] = {}
        self.calls: list[tuple] = []

    def list(self, uid):
        return [dict(r) for r in self.rows.values() if r["user_id"] == uid]

    def touch_or_create(self, uid, cid, first):
        self.calls.append((uid, cid, first))
        k = (uid, cid)
        if k not in self.rows:
            self.rows[k] = {"user_id": uid, "context_id": cid, "title": (first or "")[:20],
                            "created_at": 0, "updated_at": 0}
        else:
            self.rows[k]["updated_at"] = 1
        return self.rows[k]["title"]

    def rename(self, uid, cid, title):
        k = (uid, cid)
        if k in self.rows and (title or "").strip():
            self.rows[k]["title"] = title.strip(); return True
        return False

    def delete(self, uid, cid):
        return self.rows.pop((uid, cid), None) is not None

    def get_messages(self, uid, cid):
        return self.messages.get((uid, cid), [])


def _fake_provider():
    def factory(text, context_id, user_id=None):
        async def gen():
            yield {"kind": "token", "text": "ok"}
            yield {"kind": "done"}
        return gen()
    return factory


def _app(tmp_path, conversation_store=None):
    store = UserStore(tmp_path / "users.db")
    auth = AuthContext(enabled=True, store=store, secret="s" * 32,
                       cookie_name="a2a_cs_uid", token_ttl_seconds=3600)
    return create_app(provider_factory=_fake_provider(), auth=auth, conversation_store=conversation_store)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _login(hc, username="alice"):
    r = await hc.post("/api/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 200


async def test_anon_blocked_on_conversation_endpoints(tmp_path):
    app = _app(tmp_path, FakeConvStore())
    async with _client(app) as hc:
        assert (await hc.get("/api/conversations")).status_code == 401
        assert (await hc.get("/api/messages?context_id=x")).status_code == 401
        assert (await hc.post("/api/conversations/rename", json={"context_id": "x", "title": "t"})).status_code == 401


async def test_chat_upserts_conversation_and_lists(tmp_path):
    cstore = FakeConvStore()
    app = _app(tmp_path, cstore)
    async with _client(app) as hc:
        await _login(hc)
        r = await hc.post("/api/chat", json={"message": "如何部署 RAGFlow", "context_id": "cX"})
        assert r.status_code == 200
        assert any(c[1] == "cX" and c[2] == "如何部署 RAGFlow" for c in cstore.calls)
        # 列表应出现该会话
        items = (await hc.get("/api/conversations")).json()["conversations"]
        assert items and items[0]["context_id"] == "cX"
        assert items[0]["title"] == "如何部署 RAGFlow"


async def test_rename_delete_and_404(tmp_path):
    cstore = FakeConvStore()
    app = _app(tmp_path, cstore)
    async with _client(app) as hc:
        await _login(hc)
        await hc.post("/api/chat", json={"message": "第一句", "context_id": "c1"})
        r = await hc.post("/api/conversations/rename", json={"context_id": "c1", "title": "我的标题"})
        assert r.status_code == 200 and r.json()["ok"] is True
        items = (await hc.get("/api/conversations")).json()["conversations"]
        assert items[0]["title"] == "我的标题"
        bad = await hc.post("/api/conversations/rename", json={"context_id": "nope", "title": "x"})
        assert bad.status_code == 404
        d = await hc.post("/api/conversations/delete", json={"context_id": "c1"})
        assert d.status_code == 200 and d.json()["ok"] is True
        assert (await hc.get("/api/conversations")).json()["conversations"] == []


async def test_messages_endpoint_returns_history(tmp_path):
    cstore = FakeConvStore()
    app = _app(tmp_path, cstore)
    async with _client(app) as hc:
        await _login(hc)
        me = (await hc.get("/api/me")).json()
        cstore.messages[(me["user_id"], "c1")] = [{"role": "user", "text": "早"}, {"role": "assistant", "text": "您好"}]
        msgs = (await hc.get("/api/messages?context_id=c1")).json()["messages"]
        assert [m["text"] for m in msgs] == ["早", "您好"]


async def test_no_store_list_empty_rename_501(tmp_path):
    app = _app(tmp_path, conversation_store=None)   # 未注入 store
    async with _client(app) as hc:
        await _login(hc)
        assert (await hc.get("/api/conversations")).json() == {"conversations": []}
        assert (await hc.post("/api/conversations/rename", json={"context_id": "c", "title": "t"})).status_code == 501
