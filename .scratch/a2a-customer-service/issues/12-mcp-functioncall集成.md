Type: grilling
Status: resolved
Blocked by: 05, 06

## Question

MCP + Function Calling 怎么并进现有 A2A + RAG，凑成「Function Call → MCP → Agent → A2A」四层一体的一个项目？（接力会话新增决策，扩地图范围）

现状：已有 A2A（接待/检索/答复三 Agent）+ RAG（Milvus/BGE-M3）+ 一个薄编排接待；缺 MCP、缺真正的 function-calling agent 循环。目标：不推翻已有，把两层补进来，且 09/10 收尾时文档描述的是最终四层形态。

待定：
- 四层做到什么深度（最小可演示 vs 完整）。
- MCP 工具集有哪些、哪些真做哪些 mock。
- 接待的 function-calling 循环形态（单轮 tool-call vs 多轮 ReAct + 步数上限）。
- MCP server 的部署形态（独立进程 stdio vs 进程内）。
- MCP 与 A2A 怎么共存不冗余（kb_search 工具是否落到检索 A2A Agent）。
- 依赖与环境（mcp/fastmcp 装进项目 .venv；qwen tool-calling 兼容性）。
- 对既有工单/测试的影响（接待编排重构、新增 MCP 工单）。

依赖：05（接待编排现状）/ 06（交互契约）已 resolved。

## Answer

MCP + Function Calling 集成定稿。目标：在**不推翻**已有 A2A+RAG 的前提下，把项目补成 **Function Call → MCP → Agent → A2A 四层一体**的同一个项目；09/10 收尾描述的是这个最终形态。

### 1. 拓扑（四层怎么串）
```
浏览器 → UI(Starlette BFF) → 接待 Agent(A2A server, 唯一入口)
   接待 = ReAct agent，用 function-calling 选工具；工具清单来自 MCP server
   MCP 工具：kb_search(真) / create_ticket / get_order_status / escalate_human(mock)
     · kb_search 内部桥接 → 检索 Agent(A2A) → Milvus RAG
   ReAct 收敛后 → 接待再 A2A 调 答复 Agent 合成最终 grounded 答复
   → 流式回 UI（token + trace + done）
```
三 Agent 全保留，复用已建 03/04；一次请求真正穿过 UI→A2A→ReAct→MCP→(A2A)检索→RAG→(A2A)答复。

### 2. MCP server
- **独立进程 + stdio**；**只提供工具实现，不含 LLM**（推理/选工具全在接待 ReAct）。
- 接待作 MCP client，用 `StdioServerParameters(command=<venv python>, args=["-m","mcp_server"])` **自动拉起子进程**、随接待起停；server 仍可独立运行以便单测。
- 工具：`kb_search(query)`→A2A 调检索 Agent 返回 chunks；`create_ticket/get_order_status/escalate_human`→mock 假数据。
- Windows：stdio server **不得 print 到 stdout**（毁 JSON-RPC 帧），日志走 stderr。

### 3. Function Calling / ReAct 循环
- `LLMClient.chat` 扩展支持 `tools` + 解析 `tool_calls`（qwen3.8-max 走 DashScope OpenAI 兼容，支持 tools/parallel/stream）。
- 接待多轮 ReAct：模型选工具→执行→结果以 `role=tool` 回填→再决策，**步数上限配置项（默认 4）**；到上限或无更多工具则收敛。
- 失败/超限：沿用票 06 §7 兜底，任务仍 COMPLETED。

### 4. 答复合成
- ReAct 拿到工具证据（kb chunks + 工单/订单结果）后，接待 **A2A 调答复 Agent**，输入仍是票 06 的 `{query, chunks, history}`（chunks = 汇总后的工具证据），产出 grounded 答复。

### 5. trace / UI
- trace DataPart 扩展含 `tool_calls: [{round, name, args_summary, result_summary, ms}]`；UI trace 面板加"工具调用"段，状态胶囊文案随阶段变（"调用工具 kb_search…"）。

### 6. 提示词记录（票 09）
- 接待 agent 的 system prompt（含工具清单/何时调用/ReAct 规则/步数上限）纳入 `docs/prompts/reception.md`，受一致性校验。

### 7. 配置（不硬编码）
- `config/agents/reception.yaml` 加 `agent:` 段：`max_steps: 4`、`mcp_server.command`（`["-m","mcp_server"]`）、`tool_timeout_seconds`；工具→下游映射沿用 `orchestration.downstream_retrieval_url`。
- 新增 `config/mcp.yaml`（工具清单/mock 数据源）可选。

### 8. 依赖
- `uv pip install mcp==2.3.0` 进项目 `.venv`（Windows 拉 pywin32）；不新增其它重依赖。

### 9. 对既有工单的影响（增量）
- 新增 3 张实施工单：① MCP server + 工具；② LLMClient 扩 tool-calling + 接待 ReAct 改造（升级 05 的写死编排）；③ UI/trace 适配工具调用。
- 01/02/03/04/06/08 与其测试复用不动；05 接待编排被 ② 取代/增强。

### 10. 测试（票 10）
- 离线：fake MCP client / fake tool 响应，测 ReAct 选工具、多轮、步数上限、失败兜底、工具证据进答复。
- e2e：真 MCP server + qwen tool-calling + 真 Milvus 全链路（归工单 09）。

## Comments

- 2026-10-08（grilling 第 1 轮，用户「按推荐」，全部按推荐收敛）：
  - Q1 深度 = **(b) 最小但四层齐活**：接待升级为"带工具的 agent 循环"，工具由一个 MCP server 提供，A2A 三 Agent 保留；不接真实工单/订单系统。
  - Q2 工具集 = **(b)**：`kb_search` 真（包现有 Milvus 检索）；`create_ticket` / `get_order_status` / `escalate_human` 用 **mock**（返回假数据，演示多工具路由）。
  - Q3 循环 = **(b) 多轮 ReAct**，步数上限默认 4（配置项），模型可连续调工具直到产出最终答复。
  - Q4 MCP server = **(a) 独立进程 + stdio**，接待作 MCP client 连接（标准用法，体现 MCP 是跨进程协议）。
  - 后台事实核实：① qwen3.8-max 支持 OpenAI 风格 `tools`/`tool_calls`/`parallel_tool_calls`/stream（[阿里云 Function Calling 文档](https://help.aliyun.com/zh/model-studio/qwen-function-calling)）；② MCP 官方 SDK `mcp` **v2.3.0**，v2 破坏性改名 `FastMCP→MCPServer`（`from mcp.server import MCPServer`；client `from mcp import Client, StdioServerParameters`）；③ `a2a-sdk 1.2.2` **无任何 MCP 集成**，桥要自建；④ `mcp/fastmcp` 未装进 .venv；⑤ Windows stdio server **不得 print 到 stdout**（破坏 JSON-RPC 帧），日志走 stderr。
  - 第 2 轮待问：MCP 工具与 A2A 检索/答复怎么接（拓扑）、MCP server 是否含 LLM、依赖装法与 server 拉起方式。
- 2026-10-08（grilling 第 2 轮，用户「按推荐」）：
  - Q5 拓扑 = **(b)**：`kb_search` MCP 工具内部**桥接到检索 A2A Agent**（MCP 工具作为 A2A 服务的适配器）；接待 ReAct 收敛后**再 A2A 调答复 Agent** 合成最终答复。三 Agent 全保留、复用已建 04，一次请求串起 UI→接待(A2A)→ReAct→MCP→(A2A)检索→RAG→(A2A)答复。
  - Q6 MCP server 边界 = **(a)**：只提供工具实现（纯函数/桥下游），**不含 LLM**；所有推理与选工具在接待 ReAct。
  - Q7 依赖与拉起 = **(a)**：接待作 MCP client 用 `StdioServerParameters` **自动拉起 server 子进程**（`python -m mcp_server`），随接待起停；仍保留可独立运行以便单测工具。装法 `uv pip install mcp==2.3.0` 进项目 `.venv`。
  - 第 3 轮待问：对既有工单/测试的影响（新增哪些实施工单、05 怎么改）、trace/UI 如何展示工具调用、接待 agent 提示词是否纳入记录。
- 2026-10-08（grilling 第 3 轮，用户「按推荐」，本票闭环）：
  - Q8 影响 = **(a) 增量**：新增 3 张实施工单（① MCP server+工具、② LLMClient 扩 tool-calling + 接待 ReAct 改造、③ UI/trace 适配），01/02/03/04/06/08 复用，05 编排被 ② 升级。
  - Q9 trace/UI = **(b)**：trace 加 `tool_calls[]`（轮次/工具名/入参摘要/结果摘要/耗时），UI 面板加"工具调用"段 + 状态胶囊随阶段变。
  - Q10 记录 = **(b)**：接待 agent system prompt 纳入 `docs/prompts/reception.md` + 一致性校验。
  - 三轮共 10 个决策全部按推荐收敛，frontier 清空；`## Answer` 定稿，置 `resolved`，回写 `map.md`。下一步 `/to-tickets` 追加 3 张实施工单。
- 2026-10-12（工程侧调优回写，`## Answer` §3 定案文本保留不改）：§3「`LLMClient.chat` 扩展支持 tools（qwen3.8-max 走 DashScope OpenAI 兼容，支持 tools/parallel/stream）」是**能力核实**（确认走 function-calling 可行），未定案"接待 ReAct 必须用 qwen3.8-max"。handoff §6.1 提速方向（实测端到端 ~345s、LLM 占 87%：分流 186s + 答复 115s）后，接待侧 **triage + ReAct 决策模型 override 到 `deepseek-v4-flash-0731`**（走 function-calling），**答复侧仍由 `config/common.yaml:llm.model = qwen3.8-max` 合成 grounded 文本**（`agents/reply/`）。真相源变更点：`config/agents/reception.yaml:llm.model = deepseek-v4-flash-0731` + `docs/prompts/reception.md` promptspec `reception.llm_model_override` 已登记；档案正文"生效模型/参数"本轮同步回写。后续若把答复侧也换型（如做全链路 flash）需重开本票或新起 ADR。
