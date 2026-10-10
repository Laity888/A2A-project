"""偏好设置端点契约测试（离线）：注入 auth + 假 profile_store，进程内驱动 UI BFF。

覆盖：未登录 401；GET 空偏好；POST upsert 后 GET 命中；POST 空值删除该项；
未注入 store 时 GET/POST → 501。
"""

from __future__ import annotations

import httpx

from ui.app import create_app
from ui.auth import AuthContext, UserStore


class FakeProfileStore:
    def __init__(self):
        self.data: dict[str, dict[str, str]] = {}
        self.hist: dict[str, list[dict]] = {}

    def get_profile(self, uid):
        return dict(self.data.get(uid, {}))

    def update_profile(self, uid, updates):
        d = self.data.setdefault(uid, {})
        d.update({k: v for k, v in updates.items() if str(v).strip()})
        return True

    def delete_profile(self, uid, keys):
        d = self.data.get(uid, {})
        for k in keys:
            d.pop(k, None)
        return True

    def clear_profile(self, uid):
        self.data[uid] = {}

    def add_hist(self, uid, intent, query, time="2026-01-01 00:00:00"):
        self.hist.setdefault(uid, []).append({"intent": intent, "query": query, "time": time})

    def get_recent_history(self, uid, n=5):
        return list(self.hist.get(uid, []))[-n:]

    def clear_history(self, uid):
        self.hist[uid] = []


def _fake_provider():
    def factory(text, context_id, user_id=None):
        async def gen():
            yield {"kind": "done"}
        return gen()
    return factory


def _app(tmp_path, profile_store=None):
    auth = AuthContext(enabled=True, store=UserStore(tmp_path / "users.db"), secret="s" * 32,
                       cookie_name="a2a_cs_uid", token_ttl_seconds=3600)
    return create_app(provider_factory=_fake_provider(), auth=auth, profile_store=profile_store)


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _login(hc, username="alice"):
    r = await hc.post("/api/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 200


async def test_anon_blocked(tmp_path):
    app = _app(tmp_path, FakeProfileStore())
    async with _client(app) as hc:
        assert (await hc.get("/api/memory/profile")).status_code == 401
        assert (await hc.post("/api/memory/profile", json={"profile": {"a": "b"}})).status_code == 401


async def test_get_empty_then_upsert(tmp_path):
    store = FakeProfileStore()
    app = _app(tmp_path, store)
    async with _client(app) as hc:
        await _login(hc)
        assert (await hc.get("/api/memory/profile")).json() == {"profile": {}}
        r = await hc.post("/api/memory/profile", json={"profile": {"reply_style": "简洁", "deploy_mode": "Docker"}})
        assert r.status_code == 200 and r.json()["ok"] is True
        prof = (await hc.get("/api/memory/profile")).json()["profile"]
        assert prof == {"reply_style": "简洁", "deploy_mode": "Docker"}


async def test_empty_value_deletes_key(tmp_path):
    store = FakeProfileStore()
    app = _app(tmp_path, store)
    async with _client(app) as hc:
        await _login(hc)
        await hc.post("/api/memory/profile", json={"profile": {"a": "1", "b": "2"}})
        await hc.post("/api/memory/profile", json={"profile": {"a": ""}})   # 清空 a → 删除
        prof = (await hc.get("/api/memory/profile")).json()["profile"]
        assert "a" not in prof and prof.get("b") == "2"


async def test_no_store_returns_501(tmp_path):
    app = _app(tmp_path, profile_store=None)
    async with _client(app) as hc:
        await _login(hc)
        assert (await hc.get("/api/memory/profile")).status_code == 501
        assert (await hc.post("/api/memory/profile", json={"profile": {"a": "b"}})).status_code == 501


async def test_history_anon_blocked(tmp_path):
    app = _app(tmp_path, FakeProfileStore())
    async with _client(app) as hc:
        assert (await hc.get("/api/memory/history")).status_code == 401


async def test_history_lists_items(tmp_path):
    store = FakeProfileStore()
    app = _app(tmp_path, store)
    async with _client(app) as hc:
        await _login(hc)
        uid = (await hc.get("/api/me")).json()["user_id"]
        store.add_hist(uid, "deploy", "如何部署")
        store.add_hist(uid, "format", "支持什么格式")
        items = (await hc.get("/api/memory/history?limit=10")).json()["history"]
        assert [i["query"] for i in items] == ["如何部署", "支持什么格式"]


async def test_clear_history_keeps_profile(tmp_path):
    store = FakeProfileStore()
    app = _app(tmp_path, store)
    async with _client(app) as hc:
        await _login(hc)
        uid = (await hc.get("/api/me")).json()["user_id"]
        await hc.post("/api/memory/profile", json={"profile": {"reply_style": "简洁"}})
        store.add_hist(uid, "x", "问题")
        r = await hc.post("/api/memory/clear", json={"scope": "history"})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert (await hc.get("/api/memory/history")).json()["history"] == []
        assert (await hc.get("/api/memory/profile")).json()["profile"] == {"reply_style": "简洁"}


async def test_history_no_store_empty(tmp_path):
    app = _app(tmp_path, profile_store=None)
    async with _client(app) as hc:
        await _login(hc)
        assert (await hc.get("/api/memory/history")).json() == {"history": []}
