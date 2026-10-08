"""检索 Agent 的真实召回/精排（工单 03）。

对齐 edu_rag-main / 票 04：
  query → BGE-M3 hybrid（dense+sparse）召回 k → 去重取父块 → bge-reranker-large 精排 → 取 n。
模型进程内加载一份（票 04），CPU 推理用信号量限并发防 OOM（票 11 §2），启动 warmup（票 11 §6）。
参数全部来自 config/agents/retrieval.yaml，不硬编码。
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from common.config import AgentConfig, load_agent

logger = logging.getLogger(__name__)


class Retriever:
    def __init__(self, cfg: AgentConfig | None = None) -> None:
        self.cfg = cfg or load_agent("retrieval")
        r = self.cfg.settings["retrieval"]
        perf = self.cfg.settings.get("performance", {})

        self.top_k = int(r["top_k"])
        self.rerank_n = int(r["rerank_n"])
        self.dense_weight = float(r["dense_weight"])
        self.sparse_weight = float(r["sparse_weight"])
        self.nprobe = int(r.get("dense_nprobe", 10))
        self.device = r.get("embedding_device", "cpu")
        self._embed_path = r["embedding_model_path"]
        self._rerank_path = r["reranker_model_path"]

        # 结果缓存（工单：检索去重）：同 (query, k) 直接命中，省掉 BGE-M3+rerank 的 CPU 前向
        self._cache_enabled = bool(r.get("cache_enabled", True))
        self._cache_max = int(r.get("cache_max", 256))
        self._cache: "OrderedDict[tuple[str, int], list[dict]]" = OrderedDict()

        self._sem = asyncio.Semaphore(int(perf.get("infer_concurrency", 1)))
        self._warmup = perf.get("warmup", {}) or {}

        # 语义缓存（第二层）配置：集合/阈值/TTL/nprobe；enabled=false 时整套不启用
        self._sc_cfg = self.cfg.settings.get("semantic_cache", {}) or {}

        self._client = None
        self._ef = None
        self._reranker = None
        self._scache = None
        self._load()

    # ---- 结果缓存（LRU）----
    def _cache_get(self, key: tuple[str, int]) -> list[dict] | None:
        if not self._cache_enabled or key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return [dict(c) for c in self._cache[key]]  # 返回浅拷贝，防调用方改动污染缓存

    def _cache_put(self, key: tuple[str, int], value: list[dict]) -> None:
        if not self._cache_enabled:
            return
        self._cache[key] = [dict(c) for c in value]
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    # ---- 依赖加载 ----
    def _load(self) -> None:
        from milvus_model.hybrid import BGEM3EmbeddingFunction
        from pymilvus import MilvusClient
        from sentence_transformers import CrossEncoder

        m = self.cfg.common.infra.milvus
        self._client = MilvusClient(
            uri=f"http://{m.host}:{m.port}", db_name=m.database
        )
        self._client.load_collection(m.collection)

        self._ef = BGEM3EmbeddingFunction(
            self._embed_path, use_fp16=(self.device == "cuda"), device=self.device
        )
        self._reranker = CrossEncoder(self._rerank_path, device=self.device)
        logger.info(
            "检索依赖就绪：BGE-M3 + reranker loaded (device=%s, %s.%s)",
            self.device, m.database, m.collection,
        )
        if self._warmup.get("enabled"):
            self._retrieve_sync(self._warmup.get("dummy_query", "预热"), self.top_k)
            logger.info("warmup 完成")

        if self._sc_cfg.get("enabled"):
            try:
                from agents.retrieval.semantic_cache import SemanticCacheStore

                dim = int(self._ef.dim["dense"])

                def _embed_dense(text: str):
                    return self._ef([text])["dense"][0]

                self._scache = SemanticCacheStore(
                    client=self._client, embed=_embed_dense, dim=dim,
                    collection=self._sc_cfg.get("collection", "cs_qc_lzs"),
                    threshold=float(self._sc_cfg.get("threshold", 0.90)),
                    ttl_seconds=float(self._sc_cfg.get("ttl_seconds", 86400)),
                    nprobe=int(self._sc_cfg.get("dense_nprobe", 16)),
                    dtype_float16=(self.device == "cuda"),
                )
                logger.info("语义缓存就绪：collection=%s threshold=%.2f",
                            self._sc_cfg.get("collection", "cs_qc_lzs"),
                            float(self._sc_cfg.get("threshold", 0.90)))
            except Exception as exc:  # noqa: BLE001 - 缓存装配失败不影响核心检索
                logger.warning("语义缓存装配失败，退回单层缓存：%s", exc)
                self._scache = None

    # ---- 语义缓存（第二层）对外异步接口 ----
    async def cache_lookup(self, query: str, user_id: str | None = None, pref_sig: str = "") -> dict[str, Any] | None:
        if self._scache is None:
            return None
        uid = user_id or "anon"
        async with self._sem:
            return await asyncio.to_thread(self._scache.lookup, query, uid, pref_sig or "")

    async def cache_write(self, query: str, answer: str, user_id: str | None = None, pref_sig: str = "") -> None:
        if self._scache is None:
            return
        uid = user_id or "anon"
        async with self._sem:
            await asyncio.to_thread(self._scache.write, query, answer, uid, pref_sig or "")

    # ---- 同步检索核心（在线程池里跑，避免阻塞事件循环）----
    def _query_vector(self, query: str):
        emb = self._ef([query])
        dense = emb["dense"][0]
        csr = emb["sparse"][0].tocsr()
        sparse = {int(i): float(v) for i, v in zip(csr.indices, csr.data)}
        return dense, sparse

    def _hybrid(self, query: str, k: int) -> list[dict]:
        from pymilvus import AnnSearchRequest, WeightedRanker

        m = self.cfg.common.infra.milvus
        dense, sparse = self._query_vector(query)
        dr = AnnSearchRequest(
            data=[dense], anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": self.nprobe}}, limit=k,
        )
        sr = AnnSearchRequest(
            data=[sparse], anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {}}, limit=k,
        )
        res = self._client.hybrid_search(
            m.collection, reqs=[dr, sr],
            ranker=WeightedRanker(self.dense_weight, self.sparse_weight),
            limit=k,
            output_fields=["text", "parent_id", "parent_content", "source", "source_url", "category"],
        )[0]
        return [h["entity"] for h in res]

    @staticmethod
    def _unique_parents(entities: list[dict]) -> list[dict]:
        seen: set[str] = set()
        parents: list[dict] = []
        for e in entities:
            pid = e.get("parent_id")
            if pid and pid not in seen:
                seen.add(pid)
                parents.append(e)
        return parents

    def _retrieve_sync(self, query: str, k: int) -> list[dict[str, Any]]:
        if not query:
            return []
        entities = self._hybrid(query, k)
        parents = self._unique_parents(entities)
        if not parents:
            return []
        pairs = [[query, p.get("parent_content", p.get("text", ""))] for p in parents]
        scores = self._reranker.predict(pairs)
        ranked = sorted(zip(scores, parents), key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for score, p in ranked[: self.rerank_n]:
            out.append({
                "text": p.get("parent_content", p.get("text", "")),
                "source": p.get("source", ""),
                "source_url": p.get("source_url", ""),
                "category": p.get("category", ""),
                "score": float(score),
            })
        return out

    # ---- 对外异步接口（被 executor 调用）----
    async def retrieve(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = int(top_k) if top_k else self.top_k
        if not query:
            return []
        key = (query.strip(), k)
        cached = self._cache_get(key)
        if cached is not None:
            logger.info("检索缓存命中 query_len=%d k=%d", len(query), k)
            return cached
        async with self._sem:  # 票 11：限并发推理，防 6G VM OOM
            result = await asyncio.to_thread(self._retrieve_sync, query, k)
        self._cache_put(key, result)
        return [dict(c) for c in result]
