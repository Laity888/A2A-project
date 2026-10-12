# 变更历史 · 检索 retrieval（生效参数）

检索 Agent 无 LLM 提示词，此处记录影响召回/排序效果的参数版本。字段同 reception。

## retrieval 管线参数

### v1 — 2026-10-08（工单 02/03）
- 改动：对齐 `edu_rag-main` 固定分层父子切分与 hybrid+rerank 参数。
- 关联：决策票 04；实施工单 02/03。
- 参数快照：`top_k=3`、`rerank_n=2`、`dense_weight=1.0`、`sparse_weight=0.7`、父 1200/子 300/重叠 50、`infer_concurrency=1`；embedding=BGE-M3(本地,CPU)、reranker=bge-reranker-large(本地,CPU)；库/集合 `a2a_cs_lzs.cs_kb_lzs`。
- 效果证据：`tests/integration/test_retrieval_e2e.py` 真实库命中（如 "如何部署 RAGFlow"→`llm_api_key_setup` 0.96）；正式 recall@2≥0.8 回归待工单 09。

### v2 — 2026-10-09（性能优化 · 第一档）
- 改动：新增结果 LRU 缓存 —— `Retriever.retrieve` 按 `(query.strip(), top_k)` 缓存命中结果，重复/近似检索直接返回、跳过一次 CPU 上的 BGE-M3 编码 + bge-reranker-large 精排前向；命中返回浅拷贝防污染缓存。
- 新增参数：`retrieval.cache_enabled=true`、`retrieval.cache_max=256`（已登记 `docs/prompts/retrieval.md`，纳入一致性校验）。
- 动机：ReAct 单问常多次调用 `kb_search`（实测最多 6 次），每次都跑满 CPU 检索链是端到端 ~83s 的主因之一；缓存直接砍掉重复推理。
- 关联：README 已知局限「端到端延迟偏高」；本次「第一档」优化之一（另两条为答复流式 + 每轮状态事件，见 reception/reply 记录）。
- 覆盖：离线单测 `tests/unit/test_retriever_cache.py`（5 例：命中不重跑、不同 k 不串、返回拷贝、LRU 淘汰、禁用则每次跑）。真实时延复测待起服务 + VM Milvus 后补（未在关 VM 情况下臆造新数字）。
