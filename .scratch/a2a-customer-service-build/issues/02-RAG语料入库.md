# 02: RAG 语料入库管线

**What to build:** 把 RAGFlow 官方中文文档灌进 Milvus，成为检索 Agent 可查的知识库。跑一次入库脚本后，集合里有带出处元数据的切片。

**Blocked by:** 01

**Status:** done

- [x] 仅抓 `/docs` 板块（sitemap 正文约 86–87 篇，`robots.txt` 允许，不含 `/blog`），导出 Markdown（票 03）
- [x] 分层父子切分：父 1200 / 子 300 / 重叠 50，`.md` 用 Markdown 切分器（票 04）
- [x] BGE-M3（CPU）经 `milvus_model` hybrid 产 dense(1024)+sparse，写入库 `a2a_cs_lzs` / 集合 `cs_kb_lzs`，索引照票 04（dense IVF_FLAT+IP nlist=128 / sparse SPARSE_INVERTED_INDEX+IP）
- [x] 元数据照 `edu_rag-main` 并补 `source_url` / `category`（票 04）
- [x] 入库规模 ~200–300 条；脚本幂等可重跑；产出条数与样例日志

## Comments

- 2026-10-08 落地并实测（项目 `.venv`，`torch 2.9.1+cpu`）。
- 代码：`src/ingest/{settings,corpus,chunking,store,__main__}.py` + `config/ingest.yaml`；`BGEM3EmbeddingFunction` 走本地权重 `edu_rag-main/rag_qa/models/bge-m3`（免下载）。
- 新增「先归档再入库」：`--crawl-only` 把原始网页以 HTML 存进 `data/ragflow_docs/html/`（+ `.md` + `manifest.json`），`--reuse` 从归档做切分/嵌入/upsert（幂等，md5 主键去重）。`.gitignore` 已加 `data/`、`logs/`。
- 实测结果：归档 **100 篇**（/docs 共 109 条，去空壳索引后 100，≥ 票 03 预估 86–87）；切分 **2770 子块** → md5 去重后 Milvus `cs_kb_lzs` 实存 **2662** 条；`count(*)=2662`。
- 检索冒烟（进程内）：`如何部署 RAGFlow`→`docs/llm_api_key_setup`(0.707)、`文档切片长度怎么设置`→`docs/python_api_reference`(0.682)——hybrid+IP 命中正常。
- 离线单测 `tests/unit/test_ingest_{chunking,store,settings}.py` **8 passed**（不碰网络/模型/Milvus）。
- 修复：`MilvusStore.count()` 原带 `limit=1` 触发 pymilvus 2.6「count with pagination not allowed」；已去掉 limit。运行时那次崩溃只在此收尾行、发生在全部 upsert 之后，数据无损。
- FlagEmbedding 需显式装进 `.venv`（milvus-model 首用时的懒装会装错解释器）：`uv pip install FlagEmbedding==1.4.0`。
