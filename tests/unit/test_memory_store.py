"""长期记忆 & 用户偏好（MemoryStore）离线测试：假 DB-API 驱动，不碰真库、0 额度。

覆盖：偏好 upsert/get/text/delete、历史 append + 读取封顶与时序、load_context 组合、
按 user_id 隔离、以及读/写失败全程降级；make_memory_store 的关闭/初始化失败返回 None。
"""

from __future__ import annotations

import json

from agents.reception.memory import MemoryStore, make_memory_store


class _DB:
    def __init__(self):
        self.profiles: dict[str, dict[str, str]] = {}
        self.history: list[dict] = []
        self.seq = 0


class _FakeCursor:
    def __init__(self, db):
        self._db = db
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        p = params or ()
        if s.startswith("CREATE TABLE"):
            self._rows = []
        elif s.startswith("SELECT PROFILE_KEY"):
            uid = p[0]
            self._rows = [{"profile_key": k, "profile_value": v}
                          for k, v in self._db.profiles.get(uid, {}).items()]
        elif s.startswith("INSERT INTO USER_PROFILES"):
            uid, k, v, _ = p
            self._db.profiles.setdefault(uid, {})[k] = v
            self._rows = []
        elif s.startswith("DELETE FROM USER_PROFILES") and "PROFILE_KEY IN" in s:
            uid, keys = p[0], p[1:]
            d = self._db.profiles.get(uid, {})
            for k in keys:
                d.pop(k, None)
            self._rows = []
        elif s.startswith("DELETE FROM USER_PROFILES"):
            self._db.profiles[p[0]] = {}
            self._rows = []
        elif s.startswith("INSERT INTO QUERY_HISTORY"):
            uid, intent, content = p
            self._db.seq += 1
            self._db.history.append({"id": self._db.seq, "uid": uid, "intent": intent, "content": content,
                                      "time": f"2026-01-01 00:00:{self._db.seq:02d}"})
            self._rows = []
        elif s.startswith("SELECT INTENT_TYPE"):
            uid, limit = p[0], p[1]
            rows = [h for h in self._db.history if h["uid"] == uid]
            rows.sort(key=lambda r: r["id"], reverse=True)
            rows = rows[:limit]
            self._rows = [{"intent_type": r["intent"], "query_content": r["content"],
                           "query_time": r["time"]} for r in rows]
        elif s.startswith("DELETE FROM QUERY_HISTORY"):
            uid = p[0]
            self._db.history = [h for h in self._db.history if h["uid"] != uid]
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


class _BoomConn:
    def cursor(self):
        raise RuntimeError("db down")

    def close(self):
        pass


def _store(db, **kw):
    return MemoryStore(connection_factory=lambda: _FakeConn(db), **kw)


def test_profile_upsert_get_and_text():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"reply_style": "简洁", "deploy_mode": "Docker"})
    prof = st.get_profile("u1")
    assert prof["reply_style"] == "简洁" and prof["deploy_mode"] == "Docker"
    text = st.get_profile_text("u1")
    assert "reply_style: 简洁" in text
    # upsert 覆盖
    st.update_profile("u1", {"reply_style": "详细"})
    assert st.get_profile("u1")["reply_style"] == "详细"


def test_profile_empty_value_ignored():
    db = _DB()
    st = _store(db)
    assert st.update_profile("u1", {"": "x", "k": "  "}) is False   # 全空 → 不写
    assert st.get_profile("u1") == {}


def test_profile_delete():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"a": "1", "b": "2"})
    st.delete_profile("u1", ["a"])
    prof = st.get_profile("u1")
    assert "a" not in prof and prof.get("b") == "2"


def test_history_append_and_recent_chronological_capped():
    db = _DB()
    st = _store(db, inject_recent=3)
    for i in range(5):
        st.append_history("u1", f"intent{i}", f"问题{i}")
    recent = st.get_recent_history("u1")
    assert len(recent) == 3
    # 返回按时间正序（旧→新），且是最近 3 条
    assert [h["query"] for h in recent] == ["问题2", "问题3", "问题4"]
    assert recent[-1]["intent"] == "intent4"


def test_history_empty_query_skipped():
    db = _DB()
    st = _store(db)
    st.append_history("u1", "x", "   ")
    assert st.get_recent_history("u1") == []


def test_load_context_combines_profile_and_recent():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"reply_style": "简洁"})
    st.append_history("u1", "deploy", "如何部署")
    ctx = st.load_context("u1")
    assert "reply_style: 简洁" in ctx["profile_text"]
    assert ctx["recent"] == ["如何部署"]


def test_isolated_by_user_and_anon_bucket():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"k": "v1"})
    st.update_profile(None, {"k": "v_anon"})
    assert st.get_profile("u1")["k"] == "v1"
    assert st.get_profile("u2") == {}
    assert st.get_profile(None)["k"] == "v_anon"       # 未登录落 anon 桶
    assert st.get_profile("anon")["k"] == "v_anon"      # 显式 anon 同桶


def test_clear_removes_both():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"k": "v"})
    st.append_history("u1", "i", "q")
    st.clear("u1")
    assert st.get_profile("u1") == {} and st.get_recent_history("u1") == []


def test_failure_degrades_gracefully():
    st = MemoryStore(connection_factory=lambda: _BoomConn())
    assert st.get_profile("u1") == {}
    assert st.get_recent_history("u1") == []
    assert st.load_context("u1") == {"profile_text": "", "recent": []}
    st.update_profile("u1", {"k": "v"})       # 不抛
    st.append_history("u1", "i", "q")         # 不抛
    st.clear("u1")                            # 不抛


class _Cfg:
    def __init__(self, memory):
        self.settings = {"memory": memory}


def test_make_memory_store_disabled_returns_none():
    assert make_memory_store(_Cfg({"enabled": False})) is None
    assert make_memory_store(_Cfg({})) is None


def test_make_memory_store_init_failure_returns_none():
    # 非法库名在建工厂阶段即抛（不触网）→ 记忆功能关闭返回 None
    st = make_memory_store(_Cfg({"enabled": True, "mysql": {"database": "bad-name;--"}}))
    assert st is None


def test_update_profile_rejects_illegal_keys():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {
        "reply_style": "简洁",          # 合法
        "bad\x00key": "x",              # 含控制字符 → 丢
        "k" * 200: "y",                 # 超长 → 丢
    })
    prof = st.get_profile("u1")
    assert prof == {"reply_style": "简洁"}


def test_history_returns_time():
    db = _DB()
    st = _store(db)
    st.append_history("u1", "deploy", "如何部署")
    recent = st.get_recent_history("u1")
    assert recent and recent[0]["query"] == "如何部署"
    assert recent[0]["time"]            # 有格式化时间


def test_clear_history_only_wipes_history():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"k": "v"})
    st.append_history("u1", "i", "q")
    st.clear_history("u1")
    assert st.get_recent_history("u1") == []
    assert st.get_profile("u1") == {"k": "v"}          # 偏好不受影响


def test_clear_profile_only_wipes_profile():
    db = _DB()
    st = _store(db)
    st.update_profile("u1", {"k": "v"})
    st.append_history("u1", "i", "q")
    st.clear_profile("u1")
    assert st.get_profile("u1") == {}
    assert st.get_recent_history("u1")                 # 历史不受影响


def test_history_query_content_is_json_with_context():
    db = _DB()
    st = _store(db)
    st.append_history("u1", "intent", "问题", context_id="cX")
    row = db.history[0]
    payload = json.loads(row["content"])
    assert payload["query"] == "问题" and payload["context_id"] == "cX"
