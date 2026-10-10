"""MySQLUserStore 离线测试：用假 DB-API 连接驱动，不碰真 MySQL、0 额度。

覆盖建表/存在查询/插入/校验/用户名反查的编排，种子幂等，标识符白名单校验。
"""

from __future__ import annotations

import pytest

from ui.auth import hash_password
from ui.mysql_store import MySQLUserStore, _ident


class FakeCursor:
    def __init__(self, data): self._d = data; self._one = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).strip().upper()
        p = params or ()
        if s.startswith("CREATE TABLE"):
            self._one = None
        elif "SELECT 1" in s:                       # exists
            self._one = {"x": 1} if p[0] in self._d else None
        elif "PW_HASH" in s and "WHERE USERNAME" in s:  # verify
            rec = self._d.get(p[0])
            self._one = dict(rec) if rec else None
        elif "SELECT USERNAME" in s and "WHERE USER_ID" in s:  # username 反查
            self._one = next(
                ({"username": r["username"]} for r in self._d.values() if r["user_id"] == p[0]), None
            )
        elif s.startswith("INSERT"):
            uid, name, pw, salt, _ts = p
            self._d[name] = {"user_id": uid, "username": name, "pw_hash": pw, "salt": salt}
            self._one = None
        else:
            self._one = None
        return self

    def fetchone(self):
        return self._one

    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeConn:
    def __init__(self, data): self._d = data
    def cursor(self): return FakeCursor(self._d)
    def close(self): pass


def _store():
    data: dict[str, dict] = {}
    store = MySQLUserStore(connection_factory=lambda: FakeConn(data), table="users")
    return store, data


def test_mysql_create_verify_username():
    store, _ = _store()
    ok, uid = store.create_user("alice", "secret123")
    assert ok and uid
    assert store.verify("alice", "secret123") == uid
    assert store.verify("alice", "wrong") is None
    assert store.verify("ghost", "secret123") is None
    assert store.username(uid) == "alice"
    assert store.username("nope") is None


def test_mysql_duplicate_and_validation():
    store, _ = _store()
    assert store.create_user("bob", "secret123")[0] is True
    ok, err = store.create_user("bob", "another")
    assert ok is False and "已存在" in err
    assert store.create_user("eve", "12")[0] is False        # 密码太短
    assert store.create_user("  ", "secret123")[0] is False  # 空用户名


def test_mysql_verify_uses_same_hash_scheme():
    store, data = _store()
    _, _uid = store.create_user("cara", "secret123")
    # 存储的 pw_hash 应等于共享 hash_password 结果（与 SQLite 后端一致）
    rec = data["cara"]
    assert rec["pw_hash"] == hash_password("secret123", rec["salt"])


def test_mysql_seed_creates_demo_accounts_and_is_idempotent():
    store, data = _store()
    seed = [{"username": "user1", "password": "123456"}, {"username": "user2", "password": "123456"}]
    store.seed(seed)
    assert "user1" in data and "user2" in data
    assert store.verify("user1", "123456")
    before = dict(data)
    store.seed(seed)                              # 再灌一次：不报错、不重复、不改动
    assert set(data.keys()) == set(before.keys())
    assert data["user1"]["user_id"] == before["user1"]["user_id"]


def test_seed_supports_tuple_entries():
    store, data = _store()
    store.seed([("user3", "123456")])
    assert "user3" in data


def test_ident_rejects_bad_names():
    assert _ident("users", "表") == "users"
    for bad in ("users;drop", "a b", "tbl--", ""):
        with pytest.raises(ValueError):
            _ident(bad, "表")
