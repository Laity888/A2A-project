# Map: A2A 客服/工单分流作品集项目

## Destination

一份可建造的 spec —— 把「用官方 A2A 协议（`a2a-sdk`）+ 通义千问 + Milvus + docker-compose，做一个客服/工单分流的三 Agent 作品集项目」这件事，拆到每个决策都定死，可以直接交给 `/to-tickets` 拆成实施工单。

## Notes

- 领域：客服 / 工单分流。
- 技术底座（已定）：官方 A2A 协议 `a2a-sdk`（Python）· 通义千问 DashScope（OpenAI 兼容）· Milvus（向量库，已装）· MySQL · Redis · docker-compose · CentOS VM。
- 每个会话开工前先读：`docs/agents/domain.md`（领域文档消费规则）。
- 有疑问时默认调用 `grilling` + `domain-modeling`。
- 用户的既有要求（贯穿全程，不占票）：
  - 不硬编码，参数走配置文件。
  - 日志标记清楚模块 / 类 / 函数 / 行号，便于测试依赖。
  - 持续记录可复现的关键提示词（从当前到上线）。
- 节奏：1–2 周；先跑通官方 Hello World，再上业务。

## Decisions so far

- [候选客服知识库数据集有哪些](./issues/01-候选客服知识库数据集.md)：没有现成可用的中文客服 RAG 知识库；中文客服数据集均为对话训练语料。可行路是自建（开源项目文档 → FAQ → Milvus）。
- [A2A SDK 与国产模型兼容性](./issues/02-a2a-sdk与国产模型兼容性.md)：`a2a-sdk` 不依赖 Gemini / ADK，LLM 由自定义 `AgentExecutor` 决定；通义千问等国产模型均 OpenAI 兼容，可直接接入。
- [跑通官方 Hello World](./issues/05-跑通官方hello-world.md)：锚点已建立。`a2a-sdk[http-server]` 1.2.2 + Python 3.10.20（uv 管理）；官方示例已移植为配置化结构（参数全在 `config/hello_world.yaml`）；两个端点实测均通（Agent Card 200 / JSON-RPC 200，含流式 SSE）。LLM 换成通义千问，走 DashScope OpenAI 兼容接口，Key 读环境变量 `aliyun_API_KEY`，默认模型 `qwen3.8-max`（`qwen-plus` 等免费额度已耗尽）。细节见 `docs/records/hello-world-runbook.md`。→ 06 解锁。
- [知识库语料来源](./issues/03-知识库语料来源.md)：选定 **RAGFlow 官方中文文档（ragflow.com.cn）**。Apache-2.0（可公开演示 / 商用，无附加条款）；爬取导出 Markdown（`robots.txt` 允许，有 `sitemap.xml`）；仅抓 `/docs` 板块（sitemap 108 条，扣除约 21 条分类索引页，正文约 86–87 篇），不含 `/blog`；保持文档切片形态入库，不做 LLM 改写；规模约 200–300 条。→ 04 解锁。
- [RAG 管线参数](./issues/04-rag管线参数.md)：整体**对齐示例项目 `edu_rag-main`**（用户指定，出入以示例为准）。分层父子切分（父 1200 / 子 300 / 重叠 50，`.md` 用 `MarkdownTextSplitter`）；本地 **BGE-M3** 经 `milvus_model` hybrid 产出 dense(1024)+sparse，`device=cpu`；Milvus 库 `a2a_cs_lzs` / 集合 `cs_kb_lzs`（dense `IVF_FLAT`+`IP` nlist=128 / sparse `SPARSE_INVERTED_INDEX`+`IP`）；混合检索 `WeightedRanker(1.0,0.7)` 召回 k=3 → `bge-reranker-large` 重排取 n=2；元数据照项目并补 `source_url`/`category`；模型**进程内加载、跑 VM（CPU）**。参数全部落在本项目 YAML 配置。
- [Agent A2A 交互契约](./issues/06-agent-a2a交互契约.md)：**接待 Agent 是唯一对外入口 + 唯一有状态者**，作为 A2A client 依次编排 检索、答复（后两者纯 server、无状态、互不调用）。每跳各建 Task，用同一 `context_id` + `metadata.parent_task_id` 串联；机器间传 `DataPart`（分流 `{is_service,intent,reason}` / 检索结果 `{query,count,chunks[{text,source,source_url,category,score}]}` / 答复输入 `{query,chunks,history}`），最终答复用 `TextPart`；对外 SSE、内部非流式；非客服由接待 LLM 结构化判定后兜底 `COMPLETED`；下游失败 `TASK_STATE_FAILED`、检索空 `COMPLETED`+空数组，接待统一兜底；Card/skills 按职责声明（`triage`/`kb_retrieval`/`answer`）；会话按 `context_id` 存 Redis（细节留实现票）。→ 08 解锁（07 无阻塞）。
- [Web UI 形态](./issues/07-web-ui形态.md)：**FastAPI 后端 + 轻量前端**（用户定）。FastAPI 兼 **BFF + A2A client**：`create_client(streaming=True)` 调接待，把 A2A `StreamResponse` 翻译成浏览器 SSE 事件（`token`/`status`/`trace`/`error`/`done`；前端用 `fetch`+`ReadableStream`，非 `EventSource`）。UI 是**纯 A2A client**（不绕协议）；`context_id` 由 FastAPI 按浏览器会话生成并维持（对齐 06 §9 的 Redis key）。展示分两层：主区对话 + 可展开 trace 面板（分流判定 / 检索命中与出处 / 各跳耗时）；失败时用户态只看兜底话术、开发者态在 trace 标红。**补充 06 对外流**：接待需额外发中间 `TaskStatusUpdateEvent` + trace `Artifact`（`DataPart`）。端口 / 容器 → 08。
- [部署拓扑 docker-compose](./issues/08-部署拓扑docker-compose.md)：compose 只起自研服务（接待 `8001` / 检索 `8002` / 答复 `8003` 仅内网 + UI `8080` 映射宿主），Milvus / MySQL / Redis 沿用 VM `192.168.88.101` 已有实例（避免 6G VM OOM）。容器间用 **service name** 互访、三个 Agent 的 Card `public_url` 写 service name（浏览器只连 UI）。单 Dockerfile 多 target（`base` 轻依赖 / `retrieval` 含 torch+pymilvus+模型）；模型目录只读挂进 检索 容器、来源做成配置项（OOM 兜底切外置）；`config/` 只读挂载 + Key 走环境变量。**产出为决策 spec；实际 compose / Dockerfile / VM 落地交 `/to-tickets` 的实施票**（3 个 Agent 尚未实现）。
- [提示词记录规范](./issues/09-提示词记录规范.md)：`docs/prompts/` 按 Agent 分文件（`reception.md` / `retrieval.md` / `reply.md`）+ 顶层索引；每个提示词维护「当前版 + 变更历史」，含**当时的模型/参数快照**与**效果证据**（代表性样例 + 关键指标）；字段对齐运行时配置与 06 的结构化输出；范围含三 Agent 全部 LLM 提示词（分流判定、system prompt、兜底话术）；**手工维护 + 一致性校验**（比对记录与 `config/agents/*.yaml`，防漂移）；「改提示词即同 commit 更新记录」纳入 DoD；提示词一律中文；**影响效果的运行时参数（`top_k` / rerank 阈值 / Milvus 检索参数）也纳入记录**——在 `docs/prompts/` 对应 Agent 文件与提示词同条目记「当前值 + 变更历史 + 关联 commit」，一致性校验一并比对 `config/` 实际值（此边界由接力会话经用户从原「不纳入」改判为「纳入」）。
- [测试策略](./issues/10-测试策略.md)：作品集**务实分层**——`unit`（纯逻辑 / DataPart·Task 契约 schema / 接待编排分支 / 会话历史注入 / 09 一致性校验，默认跑、无外部依赖、用 **fake LLM**）+ `integration`（检索 **golden set 15–25 条**、指标 **recall@2 ≥ 0.8 + source_url 出处命中**，**自算不加 RAGAS**；A2A **进程内驱动**：`DefaultRequestHandlerV2.on_message_send`+`InMemoryTaskStore`+`EventConsumer`，或 `httpx ASGITransport(build_app())`）+ **一条 e2e happy-path**（真 LLM + 真 Milvus/Redis，`@pytest.mark.e2e` opt-in）。**四条核心分支必测**：非客服→兜底且下游 0 调用 / 下游失败→兜底 COMPLETED / 检索空→COMPLETED+空数组 / 正常→产出答复。会话多轮用 fake Redis 测 history 注入。框架 **pytest+pytest-asyncio**（venv 需补装），`tests/{unit,integration,e2e}` + markers。RAGAS 降为 e2e 可选加分项。**不配 CI；作品集包装收窄为只做 `README.md`**（转 `/to-tickets` 收尾票）。
- [性能 / 并发](./issues/11-性能并发.md)：定位**演示级低并发（≤ ~5 会话）、不专门压测**（仅一次单流 + 3 并发手动冒烟记数字进 README）。延迟大头与内存风险都在 **检索 CPU 推理**：检索 Agent **单进程、模型进程内只加载一份、用进程内信号量把并发推理数限为配置项（默认 1）**超出排队（6G VM 防 OOM 主闸门）。接待 **uvicorn 单 worker + asyncio**、`context_id` 天然隔离、不做多副本。LLM **不自建限流**（靠 06 非流式串行压并发）、超时沿用 120s、**1–2 次退避重试**兜 429/瞬时、仍失败走 06 兜底。软目标（仅展示非硬 SLA）：检索 P95 ≤ ~2s / 端到端 ≤ ~15s / 首 token ≤ ~8s。检索启动 **warm-up**、不缓存 embedding。Milvus/Redis 非瓶颈、Redis 连接池；OOM 沿用 08「模型来源可配置→外置」，本票仅新增 `infer_concurrency` 等配置项。
- [MCP + Function Calling 集成](./issues/12-mcp-functioncall集成.md)：把项目补成 **Function Call→MCP→Agent→A2A 四层一体**（同一个项目，不推翻已有）。拓扑：UI→接待(A2A 入口，升级为 **ReAct agent**)→function-calling 选工具→工具由**独立进程 stdio 的 MCP server**（只给工具、不含 LLM）提供→`kb_search` 内部**桥接检索 A2A Agent→Milvus RAG**、`create_ticket/get_order_status/escalate_human` mock→收敛后接待再 **A2A 调答复 Agent** 合成 grounded 答复→流式回 UI；三 Agent 全保留、复用 03/04。ReAct 步数上限默认 4（配置）；`LLMClient.chat` 扩 `tools`/`tool_calls`（qwen3.8-max 支持）。trace 加 `tool_calls[]`、UI 面板加"工具调用"段；接待 agent system prompt 纳入 `docs/prompts`。依赖 `uv pip install mcp==2.3.0`；Windows stdio 不 print stdout。**增量**：新增 3 张实施工单（MCP server+工具 / LLMClient+接待 ReAct 改造 / UI-trace 适配），01/02/03/04/06/08 复用、05 编排被升级。

## Not yet specified

- （无）—— 三项雾已全部处理：**性能/并发** → ticket 11 已 resolved；**测试策略** → ticket 10 已 resolved；**作品集包装**经用户收窄为「只做 `README.md`」、不作决策雾，直接作为 `/to-tickets` 的收尾实施工单。**frontier 已清空，地图 Destination 达成，可进入 `/to-tickets`。**
- 已由 `/to-tickets` 拆出 **10 张实施工单**（曳光弹竖切片，含 blocking edges）→ `../a2a-customer-service-build/issues/01–10`（01 骨架与测试脚手架 → 02 RAG 入库 → 03 检索/04 答复 → 05 接待脊柱 → 06 UI → ~~07 compose~~ → 08 记录校验 → 09 e2e 冒烟 → 10 README）。**07 部署已 dropped**（用户决定不做容器化，改本地 PyCharm 起 4 服务，见工单 07 说明；10 的阻塞已去掉 07）。
- 工单 12（MCP+FC 集成）追加 **3 张实施工单 11–13**（11 MCP server+工具 → 12 LLMClient tool-calling + 接待 ReAct 改造 → 13 UI/trace 适配工具调用）已写入 `../a2a-customer-service-build/issues/`，状态 `ready-for-agent`。
- **实施工单进度（完成）**：01–06、08–13 全部 `done`；07 部署 `dropped`（本地 PyCharm 起）。四层（Function Call+MCP+Agent+A2A）本地全链路跑通：recall@2=0.81、全链路 e2e 通过、性能冒烟已记录（端到端偏慢，已给优化方向）、`README.md` 已交付。**项目收尾完成。**

## Out of scope

- 多租户与鉴权（超出作品集范围）。
- 生产级监控 / 告警 / 弹性伸缩。
- 对接真实企业工单系统（真实 API、真实数据）。