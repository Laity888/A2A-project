"""Milvus 入库（对齐 edu_rag-main 的 vector_store schema/索引，补 source_url/category）。

纯函数（md5_id / to_sparse_dict / build_row / schema 字段名）可离线单测；
BGEM3EmbeddingFunction、MilvusClient 等重依赖在类方法内延迟导入。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

from ingest.chunking import Chunk
from ingest.settings import IngestSettings

logger = logging.getLogger(__name__)

# 字段名（顺序无关，供建 schema 与离线校验共用）
FIELD_NAMES = (
    "id",
    "text",
    "dense_vector",
    "sparse_vector",
    "parent_id",
    "parent_content",
    "source",
    "timestamp",
    "source_url",
    "category",
)

DENSE_INDEX_NAME = "dense_index"
SPARSE_INDEX_NAME = "sparse_index"


def md5_id(text: str) -> str:
    """按子块文本生成 md5 主键（实现去重 + 增量 upsert，同 edu_rag-main）。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def to_sparse_dict(csr_row: Any) -> dict[int, float]:
    """把 BGE-M3 稀疏向量的 CSR 行转成 Milvus 需要的 {列号: 权重}。"""
    csr = csr_row.tocsr() if hasattr(csr_row, "tocsr") else csr_row
    return {int(idx): float(val) for idx, val in zip(csr.indices, csr.data)}


def build_row(chunk: Chunk, dense: Any, sparse: dict[int, float]) -> dict[str, Any]:
    """组装一条与 schema 完全一致的插入记录。"""
    return {
        "id": md5_id(chunk.text),
        "text": chunk.text,
        "dense_vector": dense,
        "sparse_vector": sparse,
        "parent_id": chunk.parent_id,
        "parent_content": chunk.parent_content,
        "source": chunk.source,
        "timestamp": chunk.timestamp,
        "source_url": chunk.source_url,
        "category": chunk.category,
    }


class MilvusStore:
    def __init__(self, settings: IngestSettings) -> None:
        self.s = settings
        self._client = None
        self._ef = None

    # ---- 连接 / 库 ----
    def _connect(self):
        from pymilvus import MilvusClient

        uri = f"http://{self.s.milvus_host}:{self.s.milvus_port}"
        if self.s.ensure_database:
            sys_client = MilvusClient(uri=uri)
            try:
                existing = sys_client.list_databases()
                if self.s.database not in existing:
                    sys_client.create_database(self.s.database)
                    logger.info("已创建 Milvus 库：%s", self.s.database)
            except Exception as exc:  # noqa: BLE001 - 某些部署不支持建库
                logger.warning("建库检查跳过：%s", exc)
        self._client = MilvusClient(uri=uri, db_name=self.s.database)
        return self._client

    @property
    def client(self):
        return self._client or self._connect()

    # ---- 嵌入模型 ----
    def _embedding_function(self):
        if self._ef is None:
            from milvus_model.hybrid import BGEM3EmbeddingFunction

            use_fp16 = self.s.device == "cuda"
            self._ef = BGEM3EmbeddingFunction(
                self.s.embedding_model_path, use_fp16=use_fp16, device=self.s.device
            )
            logger.info("已加载 BGE-M3：%s device=%s", self.s.embedding_model_path, self.s.device)
        return self._ef

    @property
    def dense_dim(self) -> int:
        return int(self._embedding_function().dim["dense"])

    # ---- 集合 / 索引 ----
    def ensure_collection(self) -> None:
        from pymilvus import DataType

        client = self.client
        dim = self.dense_dim
        name = self.s.collection

        if not client.has_collection(name):
            schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=100)
            schema.add_field("text", DataType.VARCHAR, max_length=65535)
            dense_type = (
                DataType.FLOAT16_VECTOR if self.s.device == "cuda" else DataType.FLOAT_VECTOR
            )
            schema.add_field("dense_vector", dense_type, dim=dim)
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_field("parent_id", DataType.VARCHAR, max_length=100)
            schema.add_field("parent_content", DataType.VARCHAR, max_length=65535)
            schema.add_field("source", DataType.VARCHAR, max_length=255)
            schema.add_field("timestamp", DataType.VARCHAR, max_length=50)
            schema.add_field("source_url", DataType.VARCHAR, max_length=512)
            schema.add_field("category", DataType.VARCHAR, max_length=128)

            ip = client.prepare_index_params()
            ip.add_index(
                field_name="dense_vector",
                index_name=DENSE_INDEX_NAME,
                index_type="IVF_FLAT",
                metric_type="IP",
                params={"nlist": self.s.index_nlist},
            )
            ip.add_index(
                field_name="sparse_vector",
                index_name=SPARSE_INDEX_NAME,
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="IP",
                params={"drop_ratio_build": self.s.index_drop_ratio_build},
            )
            client.create_collection(collection_name=name, schema=schema, index_params=ip)
            logger.info("已创建集合：%s (dense_dim=%d)", name, dim)
        else:
            logger.info("集合已存在：%s", name)
        client.load_collection(name)

    # ---- 嵌入 + 批量 upsert ----
    def _embed(self, texts: list[str]):
        return self._embedding_function()(texts)

    def upsert_chunks(self, chunks: list[Chunk]) -> int:
        client = self.client
        name = self.s.collection
        batch = self.s.batch_size
        total = 0
        for start in range(0, len(chunks), batch):
            part = chunks[start : start + batch]
            texts = [c.text for c in part]
            emb = self._embed(texts)
            dense = emb["dense"]
            sparse = emb["sparse"]
            rows = [
                build_row(c, dense[i], to_sparse_dict(sparse[i]))
                for i, c in enumerate(part)
            ]
            client.upsert(name, rows)
            total += len(rows)
            logger.info("已 upsert %d/%d 子块", total, len(chunks))
        client.load_collection(name)
        return total

    def count(self) -> int:
        res = self.client.query(
            self.s.collection, filter="", output_fields=["count(*)"]
        )
        return int(res[0]["count(*)"]) if res else 0
