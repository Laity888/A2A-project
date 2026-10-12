# 11: MCP 工具服务器

**What to build:** 一个独立的 MCP 服务器（stdio），把知识库检索和几个 mock 业务操作作为「工具」对外提供，供接待 Agent 作为 MCP client 发现并调用。单独起这个进程后，用 MCP 客户端能列出工具、调用 `kb_search` 拿到真实检索结果、调用 mock 工具拿到假数据。

**Blocked by:** 01, 03

**Status:** done

- [x] 用 `mcp==2.3.0` 的 `MCPServer`（`from mcp.server import MCPServer`，注意 v2 已从 FastMCP 改名）建 stdio server，入口 `python -m mcp_server`，`mcp.run(transport="stdio")`
- [x] 工具 `kb_search(query)`：内部作为 **A2A client** 调检索 Agent（`config` 里的 `downstream_retrieval_url`），返回结构化 chunks（text/source_url/category/score）
- [x] mock 工具 `create_ticket(...)` / `get_order_status(order_id)` / `escalate_human(...)`：返回确定性假数据，字段合理，演示多工具路由
- [x] 工具实现无状态、**不含 LLM**；参数/下游 URL 走 `config`（新增 `config/mcp.yaml` 或复用 reception 的 orchestration 段），不硬编码
- [x] 日志只走 stderr（Windows stdio：print 到 stdout 会破坏 JSON-RPC 帧）
- [x] 离线单测：直接 import 工具函数或用内存 MCP client 调 `list_tools`/`call_tool`，断言 kb_search 结构 + mock 返回（不依赖真检索时用 fake）
- [x] 依赖：`uv pip install mcp==2.3.0` 进项目 `.venv`

## Comments

- 2026-10-08 落地（项目 `.venv`）。
- 代码：`src/mcp_server/{__init__,config,retrieval_bridge,tools,server,__main__}.py` + `config/mcp.yaml`（`retrieval_url`/`client_timeout_seconds`/`top_k`，A2A_LOCAL 下 URL 自动改 127.0.0.1）。
  - `tools.py`：`kb_search(query, retrieval=)` 可注入 fake；`create_ticket/get_order_status/escalate_human` 确定性 mock；无 LLM。
  - `retrieval_bridge.py`：`make_a2a_retrieval(url,timeout,top_k)` 作为 A2A client 发 DataPart `{query,top_k}`、取回 `{count,chunks}`；`default_retrieval()` 从 config 装配。
  - `server.py`：`MCPServer("a2a-cs-tools")` + `@mcp.tool()` 注册 4 工具；`__main__.py` 起 `mcp.run(transport="stdio")`。
- 依赖：`uv pip install mcp==2.3.0`（拉 mcp-types/pywin32/jsonschema 等）。
- 验证：
  - `tests/unit/test_mcp_tools.py` **5 passed**（kb_search 委托 fake retrieval + 3 mock 结构 + server 注册 4 工具，经 `mcp._tool_manager.list_tools()`）。
  - **stdio 真回路**：`Client(StdioServerParameters(python,-m mcp_server))` 起子进程 → `list_tools` 返回 `['kb_search','create_ticket','get_order_status','escalate_human']` → `call_tool('get_order_status')` 成功返回 content。证明是可用的独立 MCP server，不只是可 import 的函数。
  - 全量默认套件 **46 passed, 2 deselected**。
- 下一步：工单 12（LLMClient tool-calling + 接待 ReAct 改造）把这套工具接进接待的决策循环。
