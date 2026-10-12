# 提示词记录 · 检索 Agent（retrieval）

检索 Agent 不调用 LLM，因此**无提示词**；但按票 09（用户改判）它**影响效果的运行时参数
也纳入记录 + 一致性校验**。运行时唯一源是 `config/`，本文件是档案。变更历史见
`docs/records/retrieval.md`。

## 当前生效参数

```promptspec
- id: retrieval.top_k
  file: config/agents/retrieval.yaml
  path: retrieval.top_k
  value: 8
- id: retrieval.rerank_n
  file: config/agents/retrieval.yaml
  path: retrieval.rerank_n
  value: 2
- id: retrieval.cache_enabled
  file: config/agents/retrieval.yaml
  path: retrieval.cache_enabled
  value: true
- id: retrieval.cache_max
  file: config/agents/retrieval.yaml
  path: retrieval.cache_max
  value: 256
- id: retrieval.dense_weight
  file: config/agents/retrieval.yaml
  path: retrieval.dense_weight
  value: 1.0
- id: retrieval.sparse_weight
  file: config/agents/retrieval.yaml
  path: retrieval.sparse_weight
  value: 0.7
- id: retrieval.parent_chunk_size
  file: config/agents/retrieval.yaml
  path: retrieval.parent_chunk_size
  value: 1200
- id: retrieval.child_chunk_size
  file: config/agents/retrieval.yaml
  path: retrieval.child_chunk_size
  value: 300
- id: retrieval.chunk_overlap
  file: config/agents/retrieval.yaml
  path: retrieval.chunk_overlap
  value: 50
- id: retrieval.infer_concurrency
  file: config/agents/retrieval.yaml
  path: performance.infer_concurrency
  value: 1
```

## 模型

| 用途 | 权重路径（config/agents/retrieval.yaml） |
|---|---|
| embedding | `retrieval.embedding_model_path` = BAAI/bge-m3（本地，CPU） |
| reranker | `retrieval.reranker_model_path` = BAAI/bge-reranker-large（本地，CPU） |

管线：hybrid(dense+sparse, `WeightedRanker(1.0,0.7)`) 召回 `top_k=8` → 去重取父块 → 精排取 `rerank_n=2`。同 `(query, top_k)` 结果走 LRU 缓存（`cache_enabled` / `cache_max`），重复检索直接命中、省掉一次 CPU 上的 BGE-M3 编码 + rerank 前向。

## 实测效果备注

真实库命中见 `tests/integration/test_retrieval_e2e.py`；golden set recall@2 回归见工单 09，
结果 **0.81**（16 条），调优与已知难例见 `docs/records/retrieval-quality.md`。
