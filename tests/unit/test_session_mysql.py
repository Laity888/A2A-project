"""MySQL 会话短期历史（持久化记忆）离线测试：假 DB-API 连接驱动，不碰真库、0 额度。

覆盖 history/append 往返与时序、读回封顶（max_history_rounds*2）、DB 失败降级为
「读当空历史 / 写静默」，以及 make_session_store 的未知后端回落内存。
"""

from __future__ import annotations

from agents.reception.session import InMemorySessionStore, MySQLSessionStore, make_session_store


class _FakeCursor:
    def __init__(self, db):
        self._db = db
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        p = params or ()
        if s.startswith("CREATE TABLE"):
            self._rows = []
        elif s.startswith("SHOW COLUMNS") or s.startswith("ALTER TABLE"):
            self._rows = []
        elif s.startswith("SELECT ROLE, TEXT"):
            key, limit = p[0], p[1]
            rows = [r for r in self._db if r["session_key"] == key]
            rows.sort(key=lambda r: r["id"], reverse=True)
            self._rows = rows[:limit]
        elif s.startswith("INSERT"):
            key, role, text, trace, _ts = p
            nid = max((r["id"] for r in self._db), default=0) + 1
            self._db.append({"id": nid, "session_key": key, "role": role, "text": text, "trace": trace})
            self._rows = []
        else:
            self._rows = []
        return self

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, db):
        self._db = db

    def cursor(self):
        return _FakeCursor(self._db)

    def close(self):
        pass


def _store(db, max_rounds=6):
    return MySQLSessionStore(connection_factory=lambda: _FakeConn(db), max_rounds=max_rounds)


async def _turns(store, key, n):
    for i in range(n):
        await store.append(key, f"问{i}", f"答{i}")


async def test_append_history_roundtrip_chronological():
    db: list[dict] = []
    store = _store(db)
    await store.append("u1:c1", "你好", "您好，有什么可以帮您？")
    await store.append("u1:c1", "支持哪些格式", "支持 PDF/Word…")
    h = await store.history("u1:c1")
    assert [t["role"] for t in h] == ["user", "assistant", "user", "assistant"]
    assert h[0]["text"] == "你好" and h[-1]["text"] == "支持 PDF/Word…"


async def test_history_is_capped_to_max_rounds_x2():
    db: list[dict] = []
    store = _store(db, max_rounds=2)   # 只读回最近 2 轮 = 4 条
    await _turns(store, "k", 5)        # 10 条
    h = await store.history("k")
    assert len(h) == 4
    assert h[-1]["text"] == "答4" and h[0]["text"] == "问3"   # 最近的 2 轮，时序


async def test_history_isolated_by_session_key():
    db: list[dict] = []
    store = _store(db)
    await store.append("u1:c1", "A", "a")
    await store.append("u2:c1", "B", "b")
    assert await store.history("u1:c1") == [{"role": "user", "text": "A"}, {"role": "assistant", "text": "a"}]
    assert await store.history("u3:c9") == []


class _BoomConn:
    def cursor(self):
        raise RuntimeError("db down")

    def close(self):
        pass


async def test_failure_degrades_gracefully():
    store = MySQLSessionStore(connection_factory=lambda: _BoomConn(), max_rounds=6)
    assert await store.history("k") == []      # 读失败 → 空历史
    await store.append("k", "u", "a")          # 写失败 → 不抛异常


class _Cfg:
    def __init__(self, session):
        self.settings = {"session": session}
        class _C:  # make_session_store 仅 memory/redis 分支会用到 cfg.common
            pass
        self.common = _C()


def test_make_session_store_defaults_and_unknown_use_memory():
    assert isinstance(make_session_store(_Cfg({"backend": "memory"})), InMemorySessionStore)
    assert isinstance(make_session_store(_Cfg({"backend": "who_knows"})), InMemorySessionStore)


def test_make_session_store_unknown_backend_falls_back_to_memory():
    store = make_session_store(_Cfg({"backend": "bogus", "max_history_rounds": 3}))
    assert hasattr(store, "_store")            # InMemorySessionStore


def test_make_session_store_mysql_init_failure_falls_back_to_memory():
    # mysql 段缺失 host/库不可达时（无 env 口令、连不上），构造工厂失败 → 退回内存
    store = make_session_store(_Cfg({"backend": "mysql", "mysql": {"database": "bad-name;--"}}))
    assert hasattr(store, "_store")
