"""ConversationStore 离线测试：假 DB-API 连接驱动 conversations + mem_session 两张表，不碰真库。"""

from __future__ import annotations

from ui.conversation_store import ConversationStore, derive_title


class Backend:
    def __init__(self):
        self.conv: dict[tuple[str, str], dict] = {}     # (uid,cid)->row
        self.session: list[dict] = []                  # mem_session 行 {id,session_key,role,text}
        self._nid = 0

    def add_session(self, session_key, role, text, trace=None):
        self._nid += 1
        self.session.append({"id": self._nid, "session_key": session_key, "role": role, "text": text, "trace": trace})


class _Cur:
    def __init__(self, b):
        self.b = b
        self._rows: list[dict] = []
        self._one = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        p = params or ()
        self._rows, self._one, self.rowcount = [], None, 0
        if s.startswith("CREATE TABLE"):
            pass
        elif s.startswith("SELECT CONTEXT_ID, TITLE"):
            uid, limit = p[0], p[1]
            rows = [r for r in self.b.conv.values() if r["user_id"] == uid]
            rows.sort(key=lambda r: r["updated_at"], reverse=True)
            self._rows = rows[:limit]
        elif s.startswith("SELECT TITLE FROM"):
            self._one = self.b.conv.get((p[0], p[1]))
        elif s.startswith("UPDATE") and "SET UPDATED_AT" in s:
            now, uid, cid = p
            r = self.b.conv.get((uid, cid))
            if r:
                r["updated_at"] = now; self.rowcount = 1
        elif s.startswith("UPDATE") and "SET TITLE" in s:
            title, uid, cid = p
            r = self.b.conv.get((uid, cid))
            if r:
                r["title"] = title; self.rowcount = 1
        elif s.startswith("INSERT INTO CONVERSATIONS"):
            uid, cid, title, created, updated = p
            self.b.conv[(uid, cid)] = {
                "user_id": uid, "context_id": cid, "title": title,
                "created_at": created, "updated_at": updated,
            }
            self.rowcount = 1
        elif s.startswith("SELECT ROLE, TEXT"):
            sk, limit = p[0], p[1]
            rows = [r for r in self.b.session if r["session_key"] == sk]
            rows.sort(key=lambda r: r["id"])
            self._rows = rows[:limit]
        elif s.startswith("DELETE FROM CONVERSATIONS"):
            if self.b.conv.pop((p[0], p[1]), None) is not None:
                self.rowcount = 1
        elif s.startswith("DELETE FROM MEM_SESSION"):
            before = len(self.b.session)
            self.b.session = [r for r in self.b.session if r["session_key"] != p[0]]
            self.rowcount = before - len(self.b.session)
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, b):
        self.b = b

    def cursor(self):
        return _Cur(self.b)

    def close(self):
        pass


def _store(backend):
    return ConversationStore(connection_factory=lambda: _Conn(backend))


def test_derive_title():
    assert derive_title("   RAGFlow  支持   哪些格式?  ") == "RAGFlow 支持 哪些格式?"
    assert derive_title("") == "新会话"
    assert derive_title("x" * 60).endswith("…") and len(derive_title("x" * 60)) == 41


def test_touch_or_create_makes_title_from_first_message():
    b = Backend()
    st = _store(b)
    title = st.touch_or_create("u1", "c1", "如何部署 RAGFlow")
    assert title == "如何部署 RAGFlow"
    assert ("u1", "c1") in b.conv
    # 再次 touch：只刷新 updated_at，标题不变
    title2 = st.touch_or_create("u1", "c1", "换个说法")
    assert title2 == "如何部署 RAGFlow"


def test_list_ordering_desc_by_updated():
    b = Backend()
    st = _store(b)
    st.touch_or_create("u1", "a", "第一"); st.touch_or_create("u1", "b", "第二")
    b.conv[("u1", "a")]["updated_at"] = 100.0
    b.conv[("u1", "b")]["updated_at"] = 200.0
    items = st.list("u1")
    assert [i["context_id"] for i in items] == ["b", "a"]


def test_get_messages_reads_mem_session_chronological_and_scoped():
    b = Backend()
    st = _store(b)
    b.add_session("u1:c1", "user", "你好")
    b.add_session("u1:c1", "assistant", "您好")
    b.add_session("u2:c1", "user", "别人的")
    msgs = st.get_messages("u1", "c1")
    assert [m["text"] for m in msgs] == ["你好", "您好"]   # 时序 + 只取本用户 session_key


def test_get_messages_missing_table_is_empty():
    class BoomConn:
        def cursor(self):
            raise RuntimeError("no such table mem_session")
        def close(self):
            pass
    st = ConversationStore(connection_factory=lambda: BoomConn(), ensure_schema=False)
    assert st.get_messages("u1", "c1") == []


def test_rename_and_delete():
    b = Backend()
    st = _store(b)
    st.touch_or_create("u1", "c1", "标题")
    b.add_session("u1:c1", "user", "hi")
    assert st.rename("u1", "c1", "新标题") is True
    assert b.conv[("u1", "c1")]["title"] == "新标题"
    assert st.rename("u1", "nope", "x") is False          # 越权/不存在
    assert st.delete("u1", "c1") is True
    assert ("u1", "c1") not in b.conv
    assert st.get_messages("u1", "c1") == []              # 消息也被清
    assert st.delete("u1", "c1") is False                 # 二次删不到


def test_get_messages_returns_parsed_trace():
    b = Backend()
    st = _store(b)
    b.add_session("u1:c1", "assistant", "答复",
                  '{"tool_calls":[{"name":"kb_search"}],"cache_hit":false}')
    msgs = st.get_messages("u1", "c1")
    assert msgs[0]["trace"]["tool_calls"][0]["name"] == "kb_search"
    assert msgs[0]["trace"]["cache_hit"] is False


def test_get_messages_bad_trace_is_none():
    b = Backend()
    st = _store(b)
    b.add_session("u1:c1", "assistant", "答复", "not-json")
    assert st.get_messages("u1", "c1")[0]["trace"] is None
