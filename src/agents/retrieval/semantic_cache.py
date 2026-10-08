"""语义缓存（第二层，工单：双层缓存）。

接待的进程内 LRU 答案缓存是「精确/归一化匹配」的第一层；这里补「语义匹配」的第二层：
把用户问句 embed 成稠密向量，存进一个独立的 Milvus 集合 `query_cache`，下次相似问句
（余弦相似度 ≥ threshold，且未过 TTL、且同一 user_id）直接命中缓存答案。

关键约束：
- 只有检索 Agent 进程持有 BGE-M3 + Milvus 连接，所以语义缓存落在检索侧，接待经 A2A 调用。
- 按 user_id 隔离（与多用户隔离一致）：检索/写入都带 user_id 过滤，A 用户的答案不会命中给 B。
- 向量做 L2 归一化后用 IP 度量 = 余弦相似度，threshold 才有确定语义（[0,1]）。
- 缓存是「优化」不是「依赖」：所有异常都应被上层吞掉并回退到正常链路，本模块只如实抛出。

纯函数（normalize / row_id / lookup_filter / decide_hit / build_row）可离线单测；
SemanticCacheStore 接受注入的 client + embed，可用假对象离线跑通读/写编排。
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

CACHE_FIELDS = ("id", "user_id", "pref_sig", "query_text", "answer_text", "dense_vector", "created_at")


def normalize(vec: Sequence[float]) -> list[float]:
    """L2 归一化，使 IP 度量等于余弦相似度；全零向量原样返回。"""
    norm = math.sqrt(sum(float(x) * float(x) for x in vec))
    if norm == 0:
        return [float(x) for x in vec]
    return [float(x) / norm for x in vec]


def row_id(user_id: str, query: str, pref_sig: str = "") -> str:
    """同 (user_id, 偏好签名, 归一化问句) 复用同一主键 → 重复写即 upsert；换风格=新签名=新条目。"""
    key = f"{user_id}\x1f{pref_sig or ''}\x1f{query.strip().lower()}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _esc(s: str) -> str:
    """转义 Milvus 过滤表达式里的双引号与反斜杠。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def lookup_filter(user_id: str, cutoff_ts: float, pref_sig: str = "") -> str:
    """按用户 + 偏好签名 + 未过期过滤（空签名匹配存为 "" 的行）。"""
    return (
        f'user_id == "{_esc(user_id)}" and pref_sig == "{_esc(pref_sig or "")}" '
        f"and created_at > {cutoff_ts:.6f}"
    )


def decide_hit(hits: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    """从检索命中里取最佳一条，距离(IP=cos)≥threshold 才算命中，返回 {answer, score}。"""
    best: dict[str, Any] | None = None
    for h in hits or []:
        if best is None or h.get("distance", 0.0) > best.get("distance", 0.0):
            best = h
    if not best:
        return None
    score = float(best.get("distance", 0.0))
    if score < threshold:
        return None
    answer = best.get("entity", {}).get("answer_text") if "entity" in best else best.get("answer_text")
    if not answer:
        return None
    return {"answer": answer, "score": round(score, 4)}


def build_row(
    *, user_id: str, query: str, answer: str, dense: Sequence[float], created_at: float,
    pref_sig: str = "",
) -> dict[str, Any]:
    return {
        "id": row_id(user_id, query, pref_sig),
        "user_id": user_id,
        "pref_sig": pref_sig or "",
        "query_text": (query or "")[:2000],
        "answer_text": answer,
        "dense_vector": list(dense),
        "created_at": float(created_at),
    }


class SemanticCacheStore:
    """query_cache 集合上的稠密向量语义缓存读写（client + embed 注入，可离线测）。"""

    def __init__(
        self, *, client: Any, embed: Callable[[str], Sequence[float]], dim: int,
        collection: str, threshold: float = 0.90, ttl_seconds: float = 86400.0,
        nprobe: int = 16, dtype_float16: bool = False,
    ) -> None:
        self._client = client
        self._embed = embed
        self._dim = int(dim)
        self._collection = collection
        self._threshold = float(threshold)
        self._ttl = float(ttl_seconds)
        self._nprobe = int(nprobe)
        self._float16 = bool(dtype_float16)
        self._ready = False

    # ---- 建集合/索引（幂等，首次用时确保存在）----
    def ensure(self) -> None:
        if self._ready:
            return
        from pymilvus import DataType

        c = self._client
        if not c.has_collection(self._collection):
            schema = c.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=100)
            schema.add_field("user_id", DataType.VARCHAR, max_length=100)
            schema.add_field("pref_sig", DataType.VARCHAR, max_length=64)
            schema.add_field("query_text", DataType.VARCHAR, max_length=2048)
            schema.add_field("answer_text", DataType.VARCHAR, max_length=65535)
            dense_type = DataType.FLOAT16_VECTOR if self._float16 else DataType.FLOAT_VECTOR
            schema.add_field("dense_vector", dense_type, dim=self._dim)
            schema.add_field("created_at", DataType.DOUBLE)
            ip = c.prepare_index_params()
            ip.add_index(
                field_name="dense_vector", index_name="qc_dense_index",
                index_type="FLAT", metric_type="IP",
            )
            c.create_collection(collection_name=self._collection, schema=schema, index_params=ip)
            logger.info("已创建语义缓存集合：%s (dim=%d)", self._collection, self._dim)
        c.load_collection(self._collection)
        self._ready = True

    # ---- 命中查询 ----
    def lookup(self, query: str, user_id: str, pref_sig: str = "") -> dict[str, Any] | None:
        if not query:
            return None
        self.ensure()
        dense = normalize(self._embed(query))
        cutoff = time.time() - self._ttl
        res = self._client.search(
            collection_name=self._collection,
            data=[dense],
            anns_field="dense_vector",
            filter=lookup_filter(user_id, cutoff, pref_sig),
            limit=1,
            output_fields=["answer_text"],
            search_params={"metric_type": "IP", "params": {"nprobe": self._nprobe}},
        )
        hits = res[0] if res else []
        return decide_hit(hits, self._threshold)

    # ---- 写入 ----
    def write(self, query: str, answer: str, user_id: str, pref_sig: str = "") -> None:
        if not query or not answer:
            return
        self.ensure()
        dense = normalize(self._embed(query))
        row = build_row(user_id=user_id, query=query, answer=answer, dense=dense,
                        created_at=time.time(), pref_sig=pref_sig)
        self._client.upsert(self._collection, [row])
        self._client.load_collection(self._collection)  # 立即可检索
