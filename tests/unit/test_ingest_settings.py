"""工单 02 离线单测：入库参数从三份 config 正确合并（不连 Milvus / 不加载模型）。"""

from __future__ import annotations

from pathlib import Path

from ingest.settings import load_ingest_settings


def test_ingest_settings_merge():
    s = load_ingest_settings()
    # Milvus 来自 common.yaml（票 04 定的库/集合）
    assert s.database == "a2a_cs_lzs"
    assert s.collection == "cs_kb_lzs"
    assert s.milvus_host and s.milvus_port == 19530
    # 切分参数来自 retrieval.yaml（票 04）
    assert s.parent_chunk_size == 1200
    assert s.child_chunk_size == 300
    assert s.chunk_overlap == 50
    # 模型路径复用本地权重，不下载
    assert Path(s.embedding_model_path).name == "bge-m3"
    # 抓取参数来自 ingest.yaml
    assert s.allow_prefix == "/docs"
    assert "/blog" in s.exclude_prefixes
    assert s.batch_size > 0
    assert isinstance(s.output_dir, Path)
