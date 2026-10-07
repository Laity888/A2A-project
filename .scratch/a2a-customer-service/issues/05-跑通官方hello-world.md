Type: task
Status: resolved

## Question

先跑通官方 A2A Hello World，并把 LLM 换成通义千问。

要做的事：
1. 建 Python 环境，安装 `a2a-sdk`。
2. 跑官方 helloworld 示例，确认 Agent Card 端点与 JSON-RPC 端点能通。
3. 把示例里的 LLM 调用替换为通义千问（DashScope，OpenAI 兼容）。
4. 记录：可复现的启动命令、环境变量、依赖版本号。

意义：先有一个「确定能跑」的锚点，后续出问题才能分清是「协议用错」还是「业务代码错」。

## Answer

四步全部完成并实测通过。

1. 环境：Python 3.10.20（uv 管理）+ `a2a-sdk[http-server]` 1.2.2。
   注意 `a2a-sdk` 1.x 类型已改为 protobuf 生成（`TaskState.TASK_STATE_*`、
   `AgentCard.supported_interfaces`），且必须带 `[http-server]` extra，否则缺 `sse_starlette`。
2. 官方 Hello World 已移植为配置化结构：`src/hello_world/`，参数全在 `config/hello_world.yaml`。
   两个端点实测均通：`GET /.well-known/agent-card.json` → 200；
   `POST /`（JSON-RPC，非流式 + 流式 SSE）→ 200。
3. LLM 已换成通义千问：`llm_agent.py` 走 DashScope OpenAI 兼容接口。
   Key 从环境变量 `aliyun_API_KEY` 读取（不写进配置文件）。
   实测账号可用模型：`qwen3.8-max`（默认）、`deepseek-v4-pro-0813`、`qwen3.8-2.4t-a95b`；
   `qwen-plus` 等返回 403 `AllocationQuota.FreeTierOnly`（免费额度耗尽，非密钥问题）。
4. 记录：`docs/records/hello-world-runbook.md`（复现命令、环境变量、依赖版本、实测日志、8 条坑）。

两个可直接复用的结论：

- Agent Card 的 `public_url` 必须是客户端可达地址，Docker 部署时要改，否则卡片指向它自己。
- 客户端 HTTP 超时必须大于模型冷启动耗时（`qwen3.8-max` 首次约 14–15s），
  已做成配置项 `client.timeout_seconds`。

## Comments

- 2026-10-07：第 1、2、4 步完成，第 3 步代码就绪但待 Key 验证。
  - 环境：Python 3.10.20（uv 管理）+ `a2a-sdk[http-server]` 1.2.2，细节见
    `docs/records/hello-world-runbook.md`。
  - 官方 Hello World 已移植为配置化结构（`src/hello_world/`，参数全在 `config/hello_world.yaml`）。
  - 实测两个端点均通：`GET /.well-known/agent-card.json` → 200；
    `POST /`（JSON-RPC，非流式 + 流式 SSE）→ 200，artifact 正文
    `Hello, World! I have received your request (...)`。
  - 通义千问接入代码已就绪（`llm_agent.py`，DashScope OpenAI 兼容 + `qwen-plus`），
    配置守卫已验证：缺 `DASHSCOPE_API_KEY` 时启动即报错并给出补救办法。
  - 待办：填入 `.env` 的 `DASHSCOPE_API_KEY`，把 `executor.mode` 改 `llm`，端到端复跑。
- 2026-10-07：Key 来源确认为机器级环境变量 `aliyun_API_KEY`（非 `.env`）。
  先直连探活：Key 有效，但 `qwen-plus` 报 403 免费额度耗尽；
  逐个实测后改用 `qwen3.8-max`。端到端复跑通过，`executor.mode` 已切为 `llm`。
  过程中发现客户端默认超时过短导致 `A2AClientTimedOutError`，已把超时配置化（120s）。
  本票闭环，置 `resolved`。