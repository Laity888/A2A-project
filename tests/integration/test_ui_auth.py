"""多用户隔离：身份层与 UI 鉴权测试（离线，0 大模型额度）。

覆盖三层：
- UserStore（SQLite + PBKDF2）：注册/校验/重名/错密码。
- sign_token / verify_token：往返、篡改拒绝、过期失效。
- create_app（auth 开启）：未登录拦下、注册/登录发 cookie、带 cookie 时 user_id 透传给 provider、
  首页与退出行为。用 tmp_path 建独立库，fake provider 顶替接待流。
"""

from __future__ import annotations

import httpx

from ui.app import create_app
from ui.auth import AuthContext, UserStore, sign_token, verify_token


# ---------- UserStore ----------

def test_userstore_create_and_verify(tmp_path):
    store = UserStore(tmp_path / "users.db")
    ok, uid = store.create_user("alice", "secret123")
    assert ok and uid
    assert store.verify("alice", "secret123") == uid
    assert store.verify("alice", "wrong") is None
    assert store.verify("nobody", "secret123") is None


def test_userstore_rejects_duplicate_and_weak(tmp_path):
    store = UserStore(tmp_path / "users.db")
    assert store.create_user("bob", "secret123")[0] is True
    ok, err = store.create_user("bob", "another")
    assert ok is False and "已存在" in err
    assert store.create_user("carol", "12")[0] is False     # 密码太短
    assert store.create_user("  ", "secret123")[0] is False  # 空用户名


# ---------- token ----------

def test_token_roundtrip_and_tamper():
    secret = "k" * 32
    tok = sign_token("u1", secret, ttl_seconds=100, now=1000.0)
    assert verify_token(tok, secret, now=1050.0) == "u1"
    assert verify_token(tok + "x", secret, now=1050.0) is None       # 篡改
    assert verify_token("garbage", secret) is None
    assert verify_token(tok, "other-secret", now=1050.0) is None      # 换密钥
    assert verify_token(tok, secret, now=2000.0) is None             # 过期


# ---------- create_app 鉴权 ----------

class _RecordingProvider:
    """记录 provider_factory 收到的 (text, context_id, user_id)，产出最小 SSE 事件。"""
    def __init__(self): self.seen = []
    def factory(self, text, context_id, user_id=None):
        self.seen.append({"text": text, "context_id": context_id, "user_id": user_id})
        async def gen():
            yield {"kind": "token", "text": "ok"}
            yield {"kind": "done"}
        return gen()


def _app(tmp_path):
    store = UserStore(tmp_path / "users.db")
    auth = AuthContext(
        enabled=True, store=store, secret="s" * 32,
        cookie_name="a2a_cs_uid", token_ttl_seconds=3600,
    )
    rec = _RecordingProvider()
    app = create_app(provider_factory=rec.factory, auth=auth)
    return app, rec, store


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", follow_redirects=False)


async def test_chat_requires_login(tmp_path):
    app, _rec, _ = _app(tmp_path)
    async with await _client(app) as hc:
        r = await hc.post("/api/chat", json={"message": "hi"})
        assert r.status_code == 401


async def test_index_redirects_when_anon(tmp_path):
    app, _rec, _ = _app(tmp_path)
    async with await _client(app) as hc:
        r = await hc.get("/")
        assert r.status_code == 302
        assert r.headers["location"] == "/login"


async def test_register_then_chat_carries_user_id(tmp_path):
    app, rec, store = _app(tmp_path)
    async with await _client(app) as hc:
        r = await hc.post("/api/register", json={"username": "alice", "password": "secret123"})
        assert r.status_code == 200
        assert "a2a_cs_uid" in r.headers.get("set-cookie", "")
        # 登录态 cookie 由 httpx 自动带上
        r2 = await hc.get("/")
        assert r2.status_code == 200 and "text/html" in r2.headers["content-type"]
        r3 = await hc.post("/api/chat", json={"message": "怎么部署"})
        assert r3.status_code == 200
        assert rec.seen and rec.seen[-1]["user_id"]      # 透传到 provider
        uid = rec.seen[-1]["user_id"]
        assert store.verify("alice", "secret123") == uid  # 是 alice 的 user_id


async def test_login_wrong_password_rejected(tmp_path):
    app, _rec, _ = _app(tmp_path)
    async with await _client(app) as hc:
        await hc.post("/api/register", json={"username": "bob", "password": "secret123"})
        r = await hc.post("/api/login", json={"username": "bob", "password": "nope"})
        assert r.status_code == 401


async def test_two_users_get_distinct_ids(tmp_path):
    app, rec, _ = _app(tmp_path)
    async with await _client(app) as hc1:
        await hc1.post("/api/register", json={"username": "u1", "password": "secret123"})
        await hc1.post("/api/chat", json={"message": "问题A"})
        uid1 = rec.seen[-1]["user_id"]
    async with await _client(app) as hc2:
        await hc2.post("/api/register", json={"username": "u2", "password": "secret123"})
        await hc2.post("/api/chat", json={"message": "问题A"})
        uid2 = rec.seen[-1]["user_id"]
    assert uid1 and uid2 and uid1 != uid2      # 两个用户各自独立 user_id


def _app_no_reg(tmp_path):
    store = UserStore(tmp_path / "users.db")
    auth = AuthContext(
        enabled=True, store=store, secret="s" * 32,
        cookie_name="a2a_cs_uid", token_ttl_seconds=3600, registration_enabled=False,
    )
    rec = _RecordingProvider()
    return create_app(provider_factory=rec.factory, auth=auth), rec, store


async def test_register_disabled_returns_403(tmp_path):
    app, _rec, _ = _app_no_reg(tmp_path)
    async with await _client(app) as hc:
        r = await hc.post("/api/register", json={"username": "x", "password": "secret123"})
        assert r.status_code == 403


async def test_seeded_account_logins_when_registration_closed(tmp_path):
    app, rec, store = _app_no_reg(tmp_path)
    ok, uid = store.create_user("user1", "123456")   # 模拟 MySQL 内置种子账号
    assert ok
    async with await _client(app) as hc:
        r = await hc.post("/api/login", json={"username": "user1", "password": "123456"})
        assert r.status_code == 200
        assert "a2a_cs_uid" in r.headers.get("set-cookie", "")
        r2 = await hc.post("/api/chat", json={"message": "怎么部署"})
        assert r2.status_code == 200
        assert rec.seen[-1]["user_id"] == uid         # 内置账号登录后 user_id 正确透传
