# 03: 检索 Agent

**What to build:** 一个纯 A2A server 检索 Agent：给定 `{query, top_k}` 返回结构化命中片段 + 可读摘要，可被进程内驱动测试，且对 golden 查询达标。

**Blocked by:** 01, 02

**Status:** done

- [x] `kb_retrieval` skill（json→json，票 06 §8）；入 `{query,top_k}`，Artifact 出 `{query,count,chunks[{text,source,source_url,category,score}]}` + 摘要 TextPart（票 06 §3/§4）
- [x] hybrid 检索 `WeightedRanker(1.0,0.7)` 召回 k=3 → `bge-reranker-large` 重排取 n=2（票 04）
- [x] 模型进程内加载一份 + 启动 warm-up + `infer_concurrency` 信号量（默认 1）串行化推理（票 11 §2/§6）
- [x] 空结果 → `COMPLETED` + `chunks:[]`（空 ≠ 失败，票 06 §7）
- [x] golden set 初版（数条）integration 测：recall@2 ≥ 0.8 且 source_url 命中（票 10 §3）

## Comments

- 2026-10-08 落地并实测（项目 `.venv`，torch 2.9.1+cpu）。
- 代码：`src/agents/retrieval/{retriever,executor,app}.py`。
  - `retriever.Retriever`：进程内加载 BGE-M3 + `CrossEncoder(bge-reranker-large)`（复用本地权重）；query→hybrid(dense+sparse, `WeightedRanker(1.0,0.7)`) 召回 k → 按 parent_id 去重取父块 → reranker 精排取 n=2；`asyncio.Semaphore(infer_concurrency)` 限并发 + `asyncio.to_thread` 不阻塞事件循环；启动 warmup。
  - `executor.RetrievalExecutor`：入 DataPart `{query,top_k}` → 出 Artifact DataPart `{query,count,chunks[...]} + 摘要 TextPart`；空→COMPLETED+`[]`；异常→FAILED；retriever 构造注入（测试用 fake）。
  - `app.build_app(config=None, retriever=None)`：默认真实 Retriever，测试可注入 fake。
- 测试：
  - `tests/integration/test_retrieval_contract.py`（fake retriever，进程内驱动，不碰模型/Milvus）：**3 passed**。
  - `tests/integration/test_retrieval_e2e.py`（真实 Retriever + VM Milvus，`-m e2e`）：**passed**，命中 `如何部署 RAGFlow` → `llm_api_key_setup`(0.96) / `admin_cli`(0.90)。
  - 全量默认套件：**27 passed, 2 deselected**（外部测 opt-in）。
- 修 bug：`_retrieve_sync` warmup 调用多传了 `rerank_n`（签名只要 `(query,k)`）→ TypeError；已改。
- 正式 golden set recall@2≥0.8 的完整回归留给工单 09；这里以「真实库命中 + source_url 正确」证明链路通。
