# Hello World 运行记录（工单 05）

本文件记录「跑通官方 A2A Hello World + 接入通义千问」的可复现事实：命令、环境变量、依赖版本、实测结果。
目的：先有一个「确定能跑」的锚点。以后出问题，能分清是「协议/环境用错」还是「业务代码错」。

---

## 一、本次做到什么

1. 建好独立 Python 环境，装上 `a2a-sdk`。
2. 把官方 Hello World 示例移植成配置化结构，跑起来。
3. 实测两个端点都通：Agent Card 发现端点、JSON-RPC 服务端点（非流式 + 流式）。
4. LLM 换成通义千问（DashScope OpenAI 兼容接口），端到端跑通并实测。
5. 记录可复现命令、环境变量、依赖版本。

---

## 二、环境事实

- 操作系统：Windows 11
- Python：3.10.20（uv 管理的 CPython，解释器路径 `.venv\Scripts\python.exe`）
- 包管理器：uv 0.12.17
- 虚拟环境：项目根目录 `.venv`

关键版本差异（重要）：
`a2a-sdk` 1.x 的类型已改为 protobuf 生成，写法与网上大量 0.x 教程不同。
本记录里的代码按 1.2.2 的 API 编写，例如 `TaskState.TASK_STATE_WORKING`、
`AgentCard.supported_interfaces[].protocol_binding`。

---

## 三、从零复现（安装）

```
uv venv .venv --python 3.10
uv sync
```

`uv sync` 会读取 `pyproject.toml` 与 `uv.lock`，装齐依赖，并把本项目以可编辑模式装进环境，
所以 `src/` 布局下的 `hello_world` 包可以直接导入，不需要手动设 `PYTHONPATH`。

---

## 四、启动服务

```
uv run python -m hello_world
```

实测输出（已按统一日志格式打印「模块:类.函数:行号」），llm 模式：

```
2026-10-07 18:05:19 | INFO | __main__:-.main:26 | 加载配置：...\config\hello_world.yaml
2026-10-07 18:05:19 | INFO | __main__:-.main:27 | 启动服务 host=127.0.0.1 port=9999 mode=llm
2026-10-07 18:05:19 | INFO | llm_agent:DashScopeAgent.__init__:27 | 初始化通义千问客户端 provider=dashscope model=qwen3.8-max base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
2026-10-07 18:05:19 | INFO | app:-.build_app:103 | 服务已装配 mode=llm card_url=/.well-known/agent-card.json public_url=http://127.0.0.1:9999
2026-10-07 18:05:19 | INFO | server:-._log_started_message:234 | Uvicorn running on http://127.0.0.1:9999
```

`executor.mode` 支持两个值：`echo`（官方原样，固定回复，不需要 Key）与 `llm`（通义千问）。

---

## 五、验证两个端点

### 5.1 Agent Card 发现端点

PowerShell：

```
Invoke-WebRequest -Uri "http://127.0.0.1:9999/.well-known/agent-card.json" -UseBasicParsing
```

实测：HTTP 200，返回 JSON：

```
{"name":"Hello World Agent","description":"Just a hello world agent",
 "supportedInterfaces":[{"url":"http://127.0.0.1:9999","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
 "version":"0.0.1","capabilities":{"streaming":true,"extendedAgentCard":true},
 "defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],
 "skills":[{"id":"echo_bot","name":"Echo Bot", ...}]}
```

### 5.2 JSON-RPC 服务端点

```
uv run python -m hello_world.client "客户说收到的杯子碎了，要退货"
```

客户端做三件事：拉 Agent Card → 非流式发消息 → 流式发消息。

实测结果（mode=echo，验证协议本身）：

- 非流式 `POST /` → HTTP 200，Task 最终状态 `TASK_STATE_COMPLETED`
- 流式 `POST /` → HTTP 200（SSE），依次收到 status_update / artifact_update / status_update
- 回复正文：`Hello, World! I have received your request (...)`

实测结果（mode=llm，验证通义千问链路）：

- 非流式回复：`很抱歉杯子收到时碎了，我们会为您处理退货。请您先提供订单号和破损照片，方便我们尽快审核。审核通过后会为您安排退款或补发。`
- 流式回复：`非常抱歉给您带来不便，杯子碎了我们可以为您办理退货退款或补发。请您提供订单号和破损照片，我们会尽快为您处理。`

服务端侧日志（同一次 llm 请求，可对上）：

```
18:06:31 | h11_impl:-.send:477 | 127.0.0.1:63035 - "GET /.well-known/agent-card.json HTTP/1.1" 200
18:06:31 | agent_executor:HelloWorldAgentExecutor.execute:64 | 收到请求 task_id=cd4a2c78-...
18:06:31 | llm_agent:DashScopeAgent.invoke:36 | 调用模型 model=qwen3.8-max
18:06:46 | _client:AsyncHttpxClientWrapper._send_single_request:1923 | POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions "HTTP/1.1 200 OK"
18:06:46 | llm_agent:DashScopeAgent.invoke:46 | 模型返回完成 tokens=172
18:06:46 | agent_executor:HelloWorldAgentExecutor.execute:75 | 回复完成 task_id=cd4a2c78-... 长度=60
18:06:46 | h11_impl:-.send:477 | 127.0.0.1:63038 - "POST / HTTP/1.1" 200
18:06:49 | llm_agent:DashScopeAgent.invoke:46 | 模型返回完成 tokens=164
18:06:49 | h11_impl:EventSourceResponse.send:477 | 127.0.0.1:63066 - "POST / HTTP/1.1" 200
```

---

## 六、环境变量

- `aliyun_API_KEY`：阿里云百炼（DashScope）API Key。
  本机已设为**机器级环境变量**，进程直接可见，因此**不需要 `.env` 文件**。
  只有在 `executor.mode=llm` 时才会被读取。
  缺失时的行为：启动直接报错并说明补救办法，不做静默兜底。
- `.env` 是可选的本地覆盖方式（复制 `.env.example`），已在 `.gitignore` 中，不会提交。

---

## 七、模型选择（实测）

本账号当前开启「仅使用免费额度」，因此部分模型的免费额度已耗尽。
逐个实测（`https://dashscope.aliyuncs.com/compatible-mode/v1`）：

可用：

- `qwen3.8-max` —— 本项目默认。通义千问，首次冷启动约 14–15s，模型热了之后 2–5s
- `deepseek-v4-pro-0813` —— 稳定约 2.5s，回复具体（会追问破损照片）
- `qwen3.8-2.4t-a95b` —— 约 3.2–3.4s

不可用（403 `AllocationQuota.FreeTierOnly`，免费额度已用尽）：

- `qwen-plus`、`qwen-turbo`、`qwen-flash`、`qwen-long`、`qwen-max`

切换模型只需改 `config/hello_world.yaml` 里的 `llm.model` 一行。
若以后要用回 `qwen-plus` 这类模型，需要在百炼控制台关闭「仅使用免费额度」或充值。

---

## 八、依赖版本（实测锁定）

直接依赖（写在 `pyproject.toml`）：

- `a2a-sdk[http-server]` 1.2.2
- `uvicorn` 0.54.0
- `starlette` 1.7.0
- `httpx` 0.28.1
- `openai` 3.26.0
- `python-dotenv` 1.2.4
- `pyyaml` 6.0.3

关键间接依赖：

- `sse-starlette` 3.5.0（由 `a2a-sdk[http-server]` 带入）
- `protobuf` 7.36.2
- `pydantic` 2.13.5

全部间接依赖由 `uv.lock` 精确锁定。

---

## 九、代码结构

- `config/hello_world.yaml`：所有可变参数（服务地址、Agent Card、执行模式、模型参数、客户端超时）。
- `src/hello_world/config.py`：读 YAML + 环境变量，装配成强类型配置对象。
- `src/hello_world/logging_setup.py`：统一日志格式，标记「模块:类.函数:行号」。
- `src/hello_world/agent.py`：官方 Hello World Agent（固定回复，不调 LLM）。
- `src/hello_world/llm_agent.py`：通义千问 Agent（DashScope OpenAI 兼容接口）。
- `src/hello_world/agent_executor.py`：AgentExecutor，与官方示例流程一致；Agent 由构造函数注入。
- `src/hello_world/app.py`：把配置装配成 Agent Card / RequestHandler / Starlette 路由。
- `src/hello_world/__main__.py`：服务端入口。
- `src/hello_world/client.py`：冒烟客户端，验证两个端点。

两种模式共用同一份 `execute()` 逻辑，只在 `agent.py` 与 `llm_agent.py` 之间切换。

---

## 十、踩到的坑

1. 官方示例已从 A2A 主仓库迁到 `a2a-samples` 仓库；
   `a2a-protocol.org` 教程正文用 `--8<--` 从 `a2a-samples` 内联，直接照抄旧教程会与 1.x API 不符。
2. 只装 `a2a-sdk` 会缺 `sse_starlette`，导入 `a2a.server.routes` 直接失败；
   必须装 `a2a-sdk[http-server]`。
3. `a2a-sdk` 1.x 的 `AgentCard` / `AgentSkill` / `TaskState` 都是 protobuf 类型，
   字段名与 0.x 不同（`supported_interfaces`、`TASK_STATE_*`）。
4. Agent Card 里的 `public_url` 必须是客户端能访问到的地址。
   本机用 `http://127.0.0.1:9999`；将来上 Docker 必须改成外部可达地址，否则客户端拿到的卡片指向它自己。
5. `uvicorn.run()` 默认会重配日志，把统一格式冲掉；传 `log_config=None` 才能保留本项目的日志格式。
6. 日志里的「类名」不是 logging 原生字段，需要在 Formatter 里遍历调用栈补出来；
   遍历时必须跳过 logging / uvicorn 等转发帧，否则会把日志过滤器自身当成调用方。
7. 客户端默认 HTTP 超时（约 5s）短于模型冷启动耗时，会报 `A2AClientTimedOutError`；
   已把超时做成配置项 `client.timeout_seconds`（当前 120s）。
   排查要点：这类超时只说明「客户端等不及」，服务端日志可能仍在正常完成，先看服务端日志再下结论。
8. 账号「仅使用免费额度」模式下，额度耗尽的模型返回的是 **403** 而不是 401，
   容易误判成 Key 无效；看到 `AllocationQuota.FreeTierOnly` 就是额度问题，不是密钥问题。

---

## 十一、下一步

- 工单 05 已闭环。下一个可开工的票：03（知识库语料来源）、07（Web UI 形态）；
  06（Agent 间 A2A 交互契约）依赖 05，现在已解锁。
- 提示词记录：`llm.system_prompt` 已进入配置文件，后续按地图要求持续累积可复现的关键提示词。