# 06: Web UI（BFF + A2A client + 前端）

**What to build:** 浏览器里和客服对话、看得到协作过程。BFF 把 A2A 流翻译成浏览器 SSE，前端渲染对话主区 + trace 面板。

**Blocked by:** 05

**Status:** done

- [x] BFF 兼 A2A client：`create_client(streaming=True)` 调接待（票 07）—— 注：用 **Starlette** 而非 FastAPI，以兼容 a2a-sdk 锁定的 starlette 1.7（FastAPI 会强制降级 starlette，冲突）；功能等价。
- [x] 把 `StreamResponse` 翻成浏览器 SSE 事件 `token / status / trace / error / done`；前端 `fetch` + `ReadableStream`（非 EventSource）（票 07）
- [x] `context_id` 由后端按浏览器会话生成并经 cookie 维持（对齐票 06 §9）
- [x] 两层展示：主区对话 + 可展开 trace 面板（分流判定 / 检索命中与出处 / 各跳耗时）；失败态给 error 事件（票 07）
- [x] UI 监听 host/port 来自 `config/ui.yaml`（8080）—— 真正 `docker compose up` 起全栈看流式+trace 归工单 07/09。

## Comments

- 2026-10-08 落地（项目 `.venv`）。工单06 契约 **5 passed**；全量默认 **37 passed, 2 deselected**。
- 代码：`src/ui/{config,sse,translate,provider,app}.py` + `src/ui/static/index.html` + `config/ui.yaml`。
  - `sse.format_sse`：纯函数，`event: <kind>\ndata: <json>\n\n`。
  - `translate.a2a_to_events`：A2A 流 chunk → 规范化事件（status/token/trace）。
  - `provider.A2AReceptionProvider`：`create_client(streaming=True)` 连接待、逐块翻译、结束 `done`、异常 `error`。运行时才连真接待；测试注入 fake provider_factory。
  - `app.create_app(config=None, *, provider_factory=None)`：Starlette；`POST /api/chat`（SSE，context_id 走 cookie）+ `GET /`（前端页）。
  - 前端：单文件自包含，`fetch`+`ReadableStream` 解析 SSE、对话气泡、trace 面板可展开/收起、错误红字。
- 测试（离线，注入 fake provider）：SSE 格式、事件翻译、session cookie 下发、异常→error 事件、静态页返回。
- **移交**：真实浏览器 demo 需接待服务在线（`docker compose up`）→ 工单 07（部署）+ 09（全链路 e2e）。
