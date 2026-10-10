"""答案缓存单测（工单：重复问题省算力）。纯内存 + 可注入时钟，不依赖网络/模型。"""

from __future__ import annotations

from agents.reception.answer_cache import AnswerCache


def test_set_and_get_roundtrip():
    c = AnswerCache()
    c.set("RAGFlow 支持哪些文件格式", "支持 PDF/DOCX…")
    assert c.get("RAGFlow 支持哪些文件格式") == "支持 PDF/DOCX…"


def test_normalize_makes_paraphrase_hit():
    c = AnswerCache(normalize=True)
    c.set("RAGFlow支持哪些文件格式?", "A")
    # 空格/标点差异应归一到同一 key
    assert c.get("RAGFlow 支持哪些文件格式 ？") == "A"


def test_no_normalize_requires_exact():
    c = AnswerCache(normalize=False)
    c.set("a b", "A")
    assert c.get("a b") == "A"
    assert c.get("ab") is None


def test_disabled_cache_never_hits():
    c = AnswerCache(enabled=False)
    c.set("q", "A")
    assert c.get("q") is None
    assert len(c) == 0


def test_empty_answer_not_stored():
    c = AnswerCache()
    c.set("q", "")
    assert c.get("q") is None


def test_ttl_expiry():
    clock = {"t": 1000.0}
    c = AnswerCache(ttl_seconds=100, time_fn=lambda: clock["t"])
    c.set("q", "A")
    assert c.get("q") == "A"
    clock["t"] += 101          # 过期
    assert c.get("q") is None
    assert len(c) == 0         # 过期即删


def test_lru_evicts_oldest():
    c = AnswerCache(max_entries=2)
    c.set("a", "A"); c.set("b", "B")
    c.get("a")                 # a 变最近 → b 成最久未用
    c.set("c", "C")            # 超上限，淘汰 b
    assert c.get("a") == "A"
    assert c.get("c") == "C"
    assert c.get("b") is None


def test_user_scope_isolates_two_users():
    c = AnswerCache()  # 默认 scope=user
    c.set("怎么部署", "用户A的答案", user_id="u1")
    assert c.get("怎么部署", user_id="u1") == "用户A的答案"
    assert c.get("怎么部署", user_id="u2") is None   # 另一用户拿不到
    assert c.get("怎么部署") is None                  # 匿名桶也拿不到 u1 的


def test_user_scope_none_is_anon_bucket():
    c = AnswerCache()
    c.set("q", "匿名答案")                    # 无 user_id → anon 桶
    assert c.get("q") == "匿名答案"            # None 仍归 anon
    assert c.get("q", user_id="") == "匿名答案"  # 空串也归 anon
    assert c.get("q", user_id="u1") is None


def test_global_scope_shares_across_users():
    c = AnswerCache(scope="global")
    c.set("q", "共享答案", user_id="u1")
    assert c.get("q", user_id="u2") == "共享答案"   # 跨用户共享
    assert c.get("q") == "共享答案"


def test_scope_normalizes_across_punctuation():
    c = AnswerCache()
    c.set("RAGFlow支持哪些文件格式?", "A", user_id="u1")
    assert c.get("RAGFlow 支持哪些文件格式 ？", user_id="u1") == "A"
    assert c.get("RAGFlow 支持哪些文件格式 ？", user_id="u2") is None


def test_pref_sig_scopes_entries():
    c = AnswerCache()
    c.set("哈喽", "古风答复", user_id="u1", pref_sig="sigGu")
    assert c.get("哈喽", user_id="u1", pref_sig="sigGu") == "古风答复"   # 同签名命中
    assert c.get("哈喽", user_id="u1", pref_sig="sigTech") is None        # 换签名 → miss
    assert c.get("哈喽", user_id="u1") is None                            # 无签名也 miss


def test_empty_pref_sig_compatible_with_default():
    c = AnswerCache()
    c.set("q", "A", user_id="u1")                       # 不传 pref_sig → 默认 ""
    assert c.get("q", user_id="u1", pref_sig="") == "A"  # 显式空签名等价
