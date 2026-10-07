Type: grilling
Status: resolved

## Question

Web UI 用什么做，它和 A2A 是什么关系？

- 框架：Streamlit / Gradio / FastAPI + 前端？
- 它是不是一个 A2A client（通过 JSON-RPC 调接待 Agent）？还是直接调后端？
- 展示什么：只显示对话流？还是把 Agent 之间的协作过程（Task 流转）可视化？
- 部署：在 docker-compose 里怎么暴露端口？

## Answer

Web UI 形态定稿。架构：**浏览器 ↔ FastAPI（BFF，兼 A2A client）↔ 接待 Agent（A2A server）**。

### 1. 框架与技术形态
- **FastAPI 后端 + 轻量前端**（用户定）。
- FastAPI 干两件事：① 作为 **A2A client**（`create_client(streaming=True)`）调接待 Agent；② 作为 **BFF**，把 A2A 事件翻译成浏览器 SSE，并托管前端静态资源。
- 前端轻量（HTML/JS，或最小 React/Vite），只认 FastAPI 的简化事件，不碰 A2A 细节。
- 依赖新增：`fastapi`（`uvicorn` 已在）。

### 2. 与 A2A 的关系
- UI **是纯 A2A client**：走 JSON-RPC + SSE 调接待 Agent（06 里唯一对外 server）。
- 不直接 import 后端 Python、不绕过协议 → 端到端展示 A2A，UI 与内部实现解耦。

### 3. 三层事件流与 SSE 翻译
- FastAPI 消费接待的 `StreamResponse`，**重新包装**为浏览器友好的 SSE 事件：
  - `token`：答复正文增量
  - `status`：进度（「已分流 → 客服」「正在检索」「正在生成」）
  - `trace`：协作 trace 数据
  - `error`：异常（面向开发者）
  - `done`：本轮结束
- A2A 的 protobuf / 事件类型不出后端。
- 前端消费方式：因要 POST 消息体，用 `fetch` + `ReadableStream` 解析 SSE（**不用 `EventSource`**，它只支持 GET）。

### 4. 会话与 `context_id`
- **FastAPI 生成并维持** `context_id`：按浏览器会话（cookie / session）首次生成、后续复用。
- 前端无脑调用；`context_id` 与 06 §9 的 Redis key 一致；同一浏览器会话复用同一 `context_id` → 接待据此取历史。

### 5. 协作 trace（对 06 对外流的补充）
- **接待 Agent 在对外流里额外产出**：中间 `TaskStatusUpdateEvent`（进度）+ 一个 trace `Artifact`（`DataPart`）：
  `{intent, is_service, retrieval: {count, chunks: [{text, source, source_url, category, score}]}, timings: {triage_ms, retrieval_ms, reply_ms, total_ms}}`
- FastAPI 把这两类转成 `status` / `trace` 事件。
- **这是对 06 契约的补充**：06 只定了「最终答复 artifact」，接待需再发状态事件 + trace artifact。已在 06 的 Comments 追加指针。

### 6. UI 呈现（用户态 / 开发者态）
- 主区：对话流（流式逐字）。
- trace 面板（可展开）：分流判定、检索命中片段与出处（`source_url`/`category`/`score`）、各跳耗时。
- 失败：用户态只看兜底话术；开发者态在 trace 面板把失败环节标红。

### 7. 错误 / 兜底呈现
- 与 06 §7 一致：用户永远只看到兜底话术，不露 stack trace；技术细节进 trace 面板 + 日志。

### 不在本票范围（留给下游）
- UI 容器 / 端口 / 网络 / compose 编排 → ticket 08。
- 接待 Agent 内部实现（含 trace artifact 的具体产出）→ 实现票。

### 依赖（后续实现票使用）
- 后端：`fastapi`、`uvicorn`（已有）、`a2a-sdk` client 侧 `create_client` / `ClientConfig(streaming=True)`。
- 前端：轻量 HTML/JS（或最小 React/Vite），`fetch` + `ReadableStream` 消费 SSE。

## Comments

- 2026-10-07（grilling 第 1 轮，已收敛）：
  - Q1 框架 = **(c) FastAPI + 轻量前端**（用户改定，非推荐项）。
  - Q2 与 A2A 关系 = **(a) 纯 A2A client**：UI 走 JSON-RPC + SSE 调接待 Agent（06 里唯一对外 server）。
  - Q3 展示内容 = **(b) 对话流 + 协作过程可视化**（分层：主区对话 + 可展开 trace 面板：分流判定 / 检索命中与出处 / 各跳耗时）。
  - Q4 边界 = **(a)**：07 只定 UI 形态与它作为 A2A client 的对接方式；容器 / 端口 / 网络交 08。
  - 第 2 轮待问：`context_id` 生成与多轮维持、FastAPI ↔ 接待 的对接与 SSE 转发、协作 trace 数据来源（牵涉 06 对外流）、错误 / 兜底的 UI 呈现。
- 2026-10-07（grilling 第 2 轮，已收敛，本票闭环）：四个决策全部按推荐定。
  - Q5 `context_id` = **(a)** FastAPI 按浏览器会话生成并维持，对齐 06 §9 的 Redis key。
  - Q6 对接方式 = **(a)** FastAPI 做协议翻译层：`create_client(streaming=True)` 消费 `StreamResponse`，重包装为浏览器 SSE 事件 `token`/`status`/`trace`/`error`/`done`；前端用 `fetch`+`ReadableStream`（非 `EventSource`）。
  - Q7 trace 数据源 = **(a)** 接待在对外流额外发中间 `TaskStatusUpdateEvent` + trace `Artifact`(DataPart)；**补充 06 对外流契约**。
  - Q8 错误呈现 = **(a)** 用户态只看兜底话术、开发者态在 trace 面板标红。
  - `## Answer` 已定稿，置 `resolved`；已回写 06 的 Comments 指针与 `map.md`。