"""工单 02 离线单测：Milvus 记录构造的纯函数（不连 Milvus / 不加载模型）。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from ingest.chunking import Chunk
from ingest.store import FIELD_NAMES, build_row, md5_id, to_sparse_dict


def test_md5_id_stable_and_hex():
    t = "同一段子块文本"
    expect = hashlib.md5(t.encode("utf-8")).hexdigest()
    assert md5_id(t) == expect
    assert md5_id(t) == md5_id(t)  # 幂等 → upsert 去重
    assert len(md5_id(t)) == 32


def test_to_sparse_dict_from_csr_like():
    row = SimpleNamespace(indices=[7081, 121318], data=[0.11, 0.27])
    assert to_sparse_dict(row) == {7081: 0.11, 121318: 0.27}


def test_build_row_shape_matches_schema():
    chunk = Chunk(
        text="子块内容",
        parent_id="install_parent_0",
        parent_content="父块内容",
        source="install",
        category="quickstart",
        source_url="https://ragflow.com.cn/docs/install",
        timestamp="2026-10-08T00:00:00+00:00",
    )
    dense = [0.1, 0.2, 0.3]
    row = build_row(chunk, dense, {5: 0.9})

    assert set(row.keys()) == set(FIELD_NAMES)
    assert row["id"] == md5_id("子块内容")
    assert row["dense_vector"] == dense
    assert row["sparse_vector"] == {5: 0.9}
    assert row["source_url"] == chunk.source_url
    assert row["category"] == chunk.category


def test_schema_includes_extra_metadata():
    # 相对 edu_rag-main 新增的两列
    assert "source_url" in FIELD_NAMES and "category" in FIELD_NAMES
