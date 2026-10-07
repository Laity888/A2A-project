Type: grilling
Status: resolved
Blocked by: 05

## Question

三个 Agent（接待/分流 → 检索 → 答复）之间的 A2A 交互契约怎么定？

- 谁作为 A2A client、谁作为 server？还是链式调用（接待 → 检索 → 答复）？
- 每次交互的 Task 生命周期：一个 Task 走完全程，还是每跳一个新 Task？
- Message / Part 的形态：纯文本？结构化 JSON？文件引用？
- Artifact：检索 Agent 返回什么（原始片段 + 引用出处）？
- 分流决策：接待 Agent 如何把「非客服」请求挡掉？

依赖 05：先跑通 SDK 才有手感定这套契约。

## Answer

三个 Agent 的 A2A 交互契约定稿。**总原则：接待 Agent 是唯一对外入口、也是唯一有状态者，作为 A2A client 依次编排 检索 与 答复；检索 / 答复 是纯 A2A server、无状态、互不调用。** 全部参数走 YAML，不硬编码。

### 1. 拓扑（谁调谁）
- **接待 Agent**：对外唯一的 A2A **server**（用户 / 07 的 Web UI 只连它）；同时是 A2A **client**，依次调用 检索、答复。
- **检索 Agent**：纯 A2A **server**，只被 接待 调用。
- **答复 Agent**：纯 A2A **server**，只被 接待 调用。
- 检索 与 答复 **互不调用**；检索结果由 接待 中转。
- 理由：分流与会话上下文天然在 接待 手里；单入口符合「客服总机」心智；检索 / 答复 职责单一、可独立测试。

### 2. Task 生命周期与跨跳关联
- A2A 点对点，一个 `Task` 属于单个 server → **每跳各建一个 Task**。
- 三跳透传**同一个 `context_id`**（A2A 用它归组会话）。
- `Message.metadata` 带 `parent_task_id`（配合 `reference_task_ids`）指向上游 Task，链路可追溯。
- 一次用户请求的链路：接待 Task → 检索 Task → 答复 Task，同 `context_id`。

### 3. Message / Part 形态（混合）
- 机器间传数据用 **`DataPart`**（结构化 JSON）；最终答复用 **`TextPart`**；不使用 `raw` / `url` 文件引用。
- 载荷字段（定死，与 04 的 Milvus 元数据对齐）：
  - 分流判定结果（接待内部 LLM 输出）：`{is_service: bool, intent: str, reason: str}`
  - 接待 → 检索（DataPart）：`{query: str, top_k: int}`
  - 检索 → 接待（Artifact DataPart）：`{query: str, count: int, chunks: [{text, source, source_url, category, score}]}`
  - 接待 → 答复（DataPart）：`{query: str, chunks: [...], history: [{role, text}]}`
    - `history` 由 §9（会话上下文）推导：多轮时 接待 注入会话历史，单轮时为空数组，下游保持无状态。
  - 答复 → 接待（Artifact TextPart）：最终答复正文。

### 4. 检索 Agent 的 Artifact
- 结构化 `DataPart` 片段数组：`text`（父块原文）、`source`、`source_url`、`category`、`score`。
- 另附一个可读摘要 `TextPart`。
- 用途：答复 Agent 组装提示词、日志记录命中来源、测试断言命中条数。

### 5. 流式策略
- **对外（接待 ↔ 用户）：SSE 流式**。
- **内部跳（接待 ↔ 检索、接待 ↔ 答复）：非流式**（`ClientConfig(streaming=false)`）。
- 接待 executor：先 `await` 下游非流式结果，再向用户流式产出 artifact。

### 6. 分流决策（非客服拦截）
- 接待 Agent 用 **LLM 结构化判定**：提示词要求输出 `{is_service, intent, reason}`（提示词走配置）。
- 非客服：直接返回配置化兜底话术，置 `COMPLETED`，**不触发下游**。
- 判定理由写入日志（模块:类.函数:行号）。

### 7. 降级与状态语义
- 检索 / 答复 失败 → `TASK_STATE_FAILED`（协议原生），接待 捕获后走兜底。
- 检索为空 → `TASK_STATE_COMPLETED` + `chunks: []`（**空结果 ≠ 失败**）。
- 任一环节异常：接待 返回配置化兜底话术，任务仍 `COMPLETED`；技术细节只进日志，不透传给用户。

### 8. Agent Card / skills 声明
- 接待：skill `triage`，input / output `text/plain`。
- 检索：skill `kb_retrieval`，input `application/json`，output `application/json`。
- 答复：skill `answer`，input `application/json`，output `text/plain`。
- skill 描述写清「做什么、不做什么」，承载分流边界。

### 9. 会话上下文
- **接待 Agent 是唯一有状态者**：按 `context_id` 维护会话（Redis 存最近 N 轮），并把历史注入下游 DataPart（见 §3 的 `history`）。
- **检索 / 答复 无状态**，每次调用自带所需上下文。
- 存储细节（key 结构 / TTL / 轮数上限）留给实现票。

### 10. 配置与代码组织
- 每 Agent 一套：`config/agents/{reception,retrieval,reply}.yaml` + `src/agents/<name>/`；公共项抽 `config/common.yaml`。
- 所有参数（端口、Card、提示词、`top_k`、超时、兜底话术、轮数上限）走 YAML；Key 走环境变量 `aliyun_API_KEY`。

### 不在本票范围（留给下游）
- 端口分配 / Agent Card `public_url` / 容器间发现 → ticket 08。
- Web UI 形态（框架、是否 A2A client）→ ticket 07。
- 会话存储的具体键 / TTL → 实现票。
- RAG 管线内部参数 → ticket 04（已定）。

### 依赖（后续实现票使用）
`a2a-sdk[http-server]` 1.2.2：client 侧 `create_client` / `ClientConfig` / `A2ACardResolver`；helpers 侧 `new_data_part` / `new_data_message` / `new_data_artifact` / `new_task_from_user_message` / `get_stream_response_text`；server 侧 `RequestContext` / `EventQueue` / `TaskUpdater`。

## Comments

- 2026-10-07（grilling 第 1 轮，已收敛）：四个横切决策全部按推荐定。
  - Q1 拓扑 = **(b) 编排式**：接待 Agent 是唯一对外 A2A server，同时作为 A2A client 依次调用 检索 与 答复；检索/答复 互不调用。
  - Q2 非客服拦截 = **(a) LLM 结构化判定**（意图 + 理由），非客服直接兜底 COMPLETED，不触发下游。
  - Q3 降级 = **(a) 统一兜底**：任一环节失败 / 检索为空 → 配置化兜底话术，任务仍 COMPLETED，技术细节只进日志。
  - Q4 组织 = **(a) 每 Agent 一套**：`config/agents/{reception,retrieval,reply}.yaml` + `src/agents/<name>/`，公共项抽 `config/common.yaml`。
  - 事实依据（`a2a-sdk` 1.2.2 核实）：`Part` 为 oneof(text/raw/url/data)，**DataPart 原生支持**；`Task` 有 `context_id`/`metadata`；`Message` 有 `metadata`/`reference_task_ids`；SDK **无**内置多 Agent 编排框架，跨跳关联需自行用 `context_id`/`metadata` 约定。
  - 第 2 轮待问：Task 生命周期与跨跳关联、Message/Part 形态、检索 Artifact 内容、流式策略。
- 2026-10-07（grilling 第 2 轮，已收敛）：依赖拓扑的分支全部按推荐定。
  - Q5 Task 生命周期 = **(a)**：A2A 点对点，一个 `Task` 属于单个 server → 每跳各建 Task；三跳透传**同一 `context_id`** 归组，并在 `Message.metadata` 带 `parent_task_id`（配合 `reference_task_ids`）指向上游，链路可追溯。
  - Q6 Part 形态 = **(c) 混合**：机器间传数据用 `DataPart`（接待→检索 `{query,intent,top_k}`；接待→答复 `{query,chunks}`），最终答复用 `TextPart`；不用文件引用。
  - Q7 检索 Artifact = **(a)** 结构化 `DataPart` 片段数组（`text/parent_content, source, source_url, category, score`）+ 可读摘要 `TextPart`。
  - Q8 流式 = **(a)**：对外 SSE 流式；内部跳非流式（`ClientConfig.streaming=false`）；接待 executor 先 `await` 下游结果，再向用户流式产出 artifact。
  - 第 3 轮待问：Agent Card/skills 声明、结构化载荷字段约定、多轮会话上下文（承接 04 遗留的 Redis 会话）、下游失败的状态语义。
- 2026-10-07（grilling 第 3 轮，已收敛，全票闭环）：收尾层四个决策全部按推荐定。
  - Q9 Agent Card/skills = **(a)** 各按职责声明：接待 `triage`(text→text)、检索 `kb_retrieval`(json→json)、答复 `answer`(json→text)。
  - Q10 载荷字段 = **(a)** 定死：分流 `{is_service,intent,reason}`；接待→检索 `{query,top_k}`；检索 Artifact `{query,count,chunks[{text,source,source_url,category,score}]}`；接待→答复 `{query,chunks,history}`。
  - Q11 会话上下文 = **(a)** 接待 Agent 唯一有状态（按 `context_id`，Redis 存最近 N 轮），检索/答复 无状态；存储细节留实现票。
  - Q12 状态语义 = **(a)** 下游失败用 `TASK_STATE_FAILED`、检索空用 `COMPLETED`+空数组，接待 统一兜底。
  - 一处由 Q11 推导并显式落定（非静默假设）：接待→答复 的 DataPart 增加可选 `history` 字段承载多轮历史，单轮为空数组。
  - 三轮共 12 个决策全部按推荐收敛，`## Answer` 已定稿，置 `resolved`，回写 `map.md`。→ 08 解锁（07 无阻塞）。
- 2026-10-07（由 07 回写补充）：07 定稿时发现本票 §5/§7 的「对外流」只定了**最终答复 artifact**；为支撑 07 的协作过程可视化，**接待 Agent 的对外流需补发**：中间 `TaskStatusUpdateEvent`（进度）+ 一个 trace `Artifact`（`DataPart`：`{intent, is_service, retrieval:{count,chunks:[{text,source,source_url,category,score}]}, timings:{triage_ms,retrieval_ms,reply_ms,total_ms}}`）。此为**补充**，不改变本票其余契约。详见 `issues/07-web-ui形态.md` 的 `## Answer` §5。