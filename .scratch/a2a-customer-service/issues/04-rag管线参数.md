Type: grilling
Status: resolved
Blocked by: 03

## Question

RAG 管线的具体参数怎么定？

- 切分策略：按标题 / 段落 / 固定长度？chunk 大小与重叠多少？
- Embedding 模型：通义 text-embedding 还是本地模型？向量维度？
- 是否加 rerank？检索 top-k 取多少？
- 元数据字段：知识条目是否带来源 / 分类（供答复 Agent 引用出处）？

依赖 03：语料形态定了，才能定切分策略。

## Answer

选定 RAG 管线参数。**总原则：整体对齐示例项目 `edu_rag-main`（用户指定，凡有出入以示例项目为准）**；参数全部落在本项目 YAML 配置中，不硬编码。

### 1. 语料与切分
- 语料：RAGFlow 官方中文文档 `/docs` 板块（来自 ticket 03），保持文档切片形态，不做 LLM 改写。
- 切分策略：**分层父子切分（small-to-big）**。
  - 父块 1200 字符 / 子块 300 字符 / 重叠 50 字符。
  - `.md` 用 langchain `MarkdownTextSplitter`；其他类型用 `ChineseRecursiveTextSplitter`。
  - 只把**子块**向量化入库；父块原文写入子块 metadata 的 `parent_content`，检索命中子块后回取父块内容喂给 LLM。
  - **不做标题路径注入**（示例项目即如此；父块本身已提供上下文）。
- id 规则：父块 `doc_{i}_parent_{j}`；子块 `doc_{i}_parent_{j}_child_{k}`。

### 2. 向量化（Embedding）
- 模型：本地 **BGE-M3**（`D:\App\Python\PyWork\itheima\edu_rag-main\rag_qa\models\bge-m3`）。
- 加载：`milvus_model.hybrid.BGEM3EmbeddingFunction`，一次产出 **dense(1024) + sparse**（稀疏维度 = 词表 250002，只存非零）。
- 设备：`device=cpu`；CPU 下 `use_fp16=False`（fp16 仅 GPU 用）。

### 3. Milvus 集合与索引
- 连接：`192.168.88.101:19530`（Milvus v2.6.6）。
- 命名：Milvus 库 `a2a_cs_lzs`、集合 `cs_kb_lzs`（Milvus 不允许连字符，统一下划线）。
- Schema（`enable_dynamic_field=True`）：
  - `id` VARCHAR(100) 主键 = 子块正文的 md5
  - `text` VARCHAR(65535) 子块正文
  - `dense_vector` FLOAT_VECTOR(1024)
  - `sparse_vector` SPARSE_FLOAT_VECTOR
  - `parent_id` VARCHAR(100)、`parent_content` VARCHAR(65535)
  - `source` VARCHAR(50) 文档标识
  - `source_url` VARCHAR(512) ← 新增（引用出处）
  - `category` VARCHAR(50) ← 新增（板块分类）
  - `timestamp` VARCHAR(50)
- 索引：dense `IVF_FLAT` + `IP`（nlist=128）；sparse `SPARSE_INVERTED_INDEX` + `IP`（drop_ratio_build=0.2）。
- 写入：`upsert`（md5 主键 → 自动去重 + 增量更新）。

### 4. 检索与重排
- 混合检索：dense（nprobe=10）+ sparse 两路，融合 `WeightedRanker(1.0, 0.7)`，召回 **k=3**。
- 取唯一父块；父块数 < n 时跳过重排直接返回。
- 重排：`sentence_transformers.CrossEncoder(bge-reranker-large, device=cpu)`，默认 `max_length=512`（模型上限 514），取 **n=2**。
- 不设硬相似度阈值；检索为空或父块数不足 → 走兜底话术。

### 5. 元数据用途
- `source_url` / `category` 供答复 Agent 引用出处与展示来源。
- 初期不做 category 硬过滤，字段先留好（对应 ticket 03 遗留的「是否过滤开发向 API 页」——结论：不物理剔除，全部入库打标）。

### 6. 部署形态
- 照示例项目：模型**进程内加载**，随检索服务跑在 **VM（CentOS，CPU）**，模型目录通过 volume 挂载进容器。
- 切分/维度/索引/k/n 等参数与部署方式无关，不受影响。
- 风险备注：VM 仅 6G，fp32 双模型 + Milvus/MySQL 偏紧；若实测 OOM，备选方案为「模型推理外置到开发机」或「VM 加内存」，留 ticket 08 定稿。

### 7. 命名与配置
- Milvus 库/集合、MySQL 库统一 `_lzs` 后缀。
- 参数全部写入本项目 YAML 配置（延续 `config/hello_world.yaml` 约定），不改用示例项目的 `.ini`。

### 依赖（后续建库票使用）
`pymilvus`、`milvus-model`、`sentence-transformers`、`torch`(CPU 版)、`langchain-text-splitters`、`langchain-community`(Document) 等。

### 不属于本票（留给后续）
- BERT 查询分类器、4 种检索策略、MySQL BM25、Redis 会话 → 06 / 07。
- RAGAS 评测 → 测试票。
- 部署拓扑定稿（docker-compose）→ 08。

## Comments

- 2026-10-07（grilling 进行中）：
  - Q1 定：按 Markdown 标题层级（H2/H3）做语义切分（大小/重叠/标题注入待确认）。
  - Q2 由用户改定：embedding 用**本地 BGE-M3**（`D:\App\Python\PyWork\itheima\edu_rag-main\rag_qa\models\bge-m3`，1024 维 / 8192 token 上限），不走 DashScope text-embedding-v4。
  - Q3 由用户改定：rerank 用**本地 bge-reranker-large**（同项目 models 目录，512 token 上限），不走 gte-rerank-v2。
  - Q4 / Q5 按推荐。
  - 环境事实：VM 192.168.88.101（Milvus v2.6.6:19530 / MySQL 8.0:3306 / Redis:6379，均从开发机可达）；项目 `.venv` 尚无 torch / sentence-transformers / FlagEmbedding / pymilvus / pymysql / redis。
  - 新开待定分支：本地模型部署形态与内存、dense-only vs 混合检索、Milvus 命名（连字符不被允许）。
- 2026-10-07（resolved）：经 4 轮 grilling 收敛。用户指定「凡与示例项目 `edu_rag-main` 有出入，以示例项目为准」，故切分/索引/度量/检索参数全部改按示例项目（分层父子切分、IVF_FLAT+IP、k=3/n=2 等）；用户另定：BGE-M3 + bge-reranker-large 本地模型、dense+sparse 混合、命名统一下划线、模型进程内加载跑 VM（用户确认「照示例项目跑法即可，此前可完美跑通」）。完整参数见 `## Answer`。
- 2026-10-12（工程侧调优回写，`## Answer` §4 定案文本保留不改）：实施工单 09 落地时，2630 条 RAGFlow 中文文档语料下 **k=3 召回过窄**（golden 集 recall@2 = 0.69，`/docs/category` 索引页污染检索）；把 hybrid 召回 k 提到 **8** 后 recall@2 = 0.81（重排 `rerank_n=2` 不变）。真相源变更点：`config/agents/retrieval.yaml:retrieval.top_k = 8`、`config/mcp.yaml:mcp.top_k = 8`、`src/mcp_server/server.py` fallback 默认同步 8；`docs/prompts/retrieval.md` 档案正文本轮同步回写；本票 `## Answer` §4「k=3」保留作定案快照，本条为演化记录。后续任何"改回 k=3"的提议视为与本 Comments 冲突。