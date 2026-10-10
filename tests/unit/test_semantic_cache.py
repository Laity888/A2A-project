"""语义缓存（第二层）离线测试：纯函数 + SemanticCacheStore 用假 client/embed，不碰 Milvus/模型。

覆盖：normalize/row_id/lookup_filter/decide_hit 的边界；store 的命中/未过期过滤、
写入 upsert、首次 ensure 建集合。全部 0 额度、无外部依赖。
"""

from __future__ import annotations

from agents.retrieval.semantic_cache import (
    SemanticCacheStore, build_row, decide_hit, lookup_filter, normalize, row_id,
)


# ---------- 纯函数 ----------

def test_normalize_l2():
    assert normalize([3, 4]) == [0.6, 0.8]
    assert normalize([0, 0]) == [0.0, 0.0]      # 全零原样


def test_row_id_stable_and_user_scoped():
    assert row_id("u1", "  怎么退货 ") == row_id("u1", "怎么退货")   # 去空白+小写稳定
    assert row_id("u1", "q") != row_id("u2", "q")                    # 用户不同 → 不同主键
    assert row_id("u1", "q", "") == row_id("u1", "q")                # 空签名兼容旧行为
    assert row_id("u1", "q", "sigA") != row_id("u1", "q", "sigB")    # 换偏好签名 → 不同主键


def test_lookup_filter_escapes_quotes_and_has_cutoff():
    expr = lookup_filter('u"1', 123.0)
    assert 'user_id == "u\\"1"' in expr
    assert "created_at > 123.000000" in expr
    assert 'pref_sig == ""' in expr                       # 默认空签名
    expr2 = lookup_filter("u1", 1.0, 'a"b')
    assert 'pref_sig == "a\\"b"' in expr2                 # 签名转义


def test_decide_hit_picks_best_and_thresholds():
    hits = [
        {"distance": 0.70, "entity": {"answer_text": "低"}},
        {"distance": 0.95, "entity": {"answer_text": "高"}},
    ]
    assert decide_hit(hits, 0.90) == {"answer": "高", "score": 0.95}
    assert decide_hit(hits, 0.96) is None                 # 最高仍不够阈值
    assert decide_hit([], 0.9) is None
    assert decide_hit([{"distance": 0.99}], 0.9) is None  # 有距离无答案 → 不算命中


def test_decide_hit_supports_flat_row():
    assert decide_hit([{"distance": 0.91, "answer_text": "A"}], 0.90) == {"answer": "A", "score": 0.91}


def test_build_row_shape():
    r = build_row(user_id="u1", query="q", answer="A", dense=[1.0, 2.0], created_at=10.0)
    assert r["user_id"] == "u1" and r["answer_text"] == "A"
    assert r["dense_vector"] == [1.0, 2.0] and r["created_at"] == 10.0
    assert r["id"] == row_id("u1", "q")
    assert r["pref_sig"] == ""
    r2 = build_row(user_id="u1", query="q", answer="A", dense=[1.0], created_at=10.0, pref_sig="sX")
    assert r2["pref_sig"] == "sX" and r2["id"] == row_id("u1", "q", "sX")


# ---------- store（假 client/embed）----------

class FakeSchema:
    def __init__(self): self.fields = []
    def add_field(self, name, dtype, **kw): self.fields.append(name)


class FakeIndexParams:
    def __init__(self): self.adds = []
    def add_index(self, **kw): self.adds.append(kw)


class FakeClient:
    def __init__(self, existing=True, search_result=None):
        self.existing = existing
        self.search_result = search_result or [[]]
        self.created = None
        self.schemas = []
        self.indexes = []
        self.loaded = []
        self.upserts = []
        self.search_calls = []

    def has_collection(self, _name): return self.existing
    def create_schema(self, **_kw):
        s = FakeSchema(); self.schemas.append(s); return s
    def prepare_index_params(self):
        ip = FakeIndexParams(); self.indexes.append(ip); return ip
    def create_collection(self, collection_name=None, schema=None, index_params=None):
        self.created = collection_name; self.existing = True
    def load_collection(self, name): self.loaded.append(name)
    def search(self, **kw): self.search_calls.append(kw); return self.search_result
    def upsert(self, name, rows): self.upserts.append((name, rows))


def _store(client, embed=lambda t: [3.0, 4.0]):
    return SemanticCacheStore(
        client=client, embed=embed, dim=2, collection="cs_qc_lzs",
        threshold=0.90, ttl_seconds=100, nprobe=8,
    )


def test_store_lookup_hit_passes_normalized_dense_and_filter():
    cli = FakeClient(search_result=[[{"distance": 0.95, "entity": {"answer_text": "缓存答案"}}]])
    got = _store(cli).lookup("怎么退货", "u1")
    assert got == {"answer": "缓存答案", "score": 0.95}
    call = cli.search_calls[0]
    assert call["data"] == [[0.6, 0.8]]                 # embed 归一化后传入
    assert 'user_id == "u1"' in call["filter"]          # 按用户过滤
    assert 'pref_sig == ""' in call["filter"]           # 默认空偏好签名
    assert "created_at >" in call["filter"]             # TTL 过滤


def test_store_lookup_below_threshold_returns_none():
    cli = FakeClient(search_result=[[{"distance": 0.50, "entity": {"answer_text": "A"}}]])
    assert _store(cli).lookup("q", "u1") is None


def test_store_write_upserts_and_ensures_load():
    cli = FakeClient(existing=True)
    _store(cli).write("怎么退货", "答案", "u1")
    assert cli.upserts, "应触发 upsert"
    name, rows = cli.upserts[0]
    assert name == "cs_qc_lzs" and rows[0]["user_id"] == "u1"
    assert rows[0]["dense_vector"] == [0.6, 0.8]        # 存归一化向量
    assert "cs_qc_lzs" in cli.loaded                    # 写后 load 立即可查


def test_store_write_skips_empty():
    cli = FakeClient()
    _store(cli).write("q", "", "u1")                    # 空答案不写
    assert cli.upserts == []


def test_store_ensure_creates_collection_when_absent():
    cli = FakeClient(existing=False, search_result=[[{"distance": 0.97, "entity": {"answer_text": "X"}}]])
    got = _store(cli).lookup("q", "u1")
    assert cli.created == "cs_qc_lzs"                   # 首次用时建集合
    assert got["answer"] == "X"
    # schema 建了 7 个字段（含 pref_sig），dense 索引 FLAT/IP
    assert len(cli.schemas[0].fields) == 7
    idx = cli.indexes[0].adds[0]
    assert idx["index_type"] == "FLAT" and idx["metric_type"] == "IP"


def test_store_write_and_lookup_carry_pref_sig():
    cli = FakeClient(existing=True, search_result=[[{"distance": 0.95, "entity": {"answer_text": "科技答复"}}]])
    st = _store(cli)
    st.write("哈喽", "科技答复", "u1", pref_sig="sigTech")
    assert cli.upserts[0][1][0]["pref_sig"] == "sigTech"        # 写带签名
    st.lookup("哈喽", "u1", pref_sig="sigTech")
    assert 'pref_sig == "sigTech"' in cli.search_calls[-1]["filter"]   # 读按签名过滤
