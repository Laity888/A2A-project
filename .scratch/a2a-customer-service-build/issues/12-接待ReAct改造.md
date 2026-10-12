# 12: 接待 ReAct 改造 + LLMClient tool-calling

**What to build:** 把接待从"写死 retrieve→reply 编排"升级为**带工具调用的 ReAct agent**：接待作为 MCP client 自动拉起工具服务器、拿到工具清单，让 qwen 通过 function-calling 自主决定调用哪些工具、多轮迭代，收敛后再 A2A 调答复 Agent 合成 grounded 答复。做完，一句"我要退货，订单 X 到哪了"能触发模型自己调 `kb_search` + `get_order_status` 再作答。

**Blocked by:** 05, 11

**Status:** done

- [x] `common/llm_client.py:LLMClient.chat` 扩展支持 `tools=[...]` 并解析返回的 `message.tool_calls`（qwen3.8-max 走 DashScope OpenAI 兼容；保留无工具时的原行为）—— 新增 `chat_tools()`，`chat()` 原样保留给答复 Agent
- [x] 接待新增 MCP client：用 `StdioServerParameters` 自动拉起 `python -m mcp_server` 子进程、`list_tools` 拿工具清单、`call_tool` 执行；随接待起停（`McpToolBroker` 懒连 + AsyncExitStack）
- [x] ReAct 循环：模型选工具→执行→结果以 `role=tool` 回填→再决策；**步数上限 `agent.max_steps`（默认 4）**；无更多工具或到上限则收敛（`agents/reception/react.py`）
- [x] 收敛后：接待 **A2A 调答复 Agent**，输入 `{query, chunks, history}`（chunks = kb_search 证据汇总），产出最终答复
- [x] 分流（非客服）仍先兜底、不进入工具循环；下游/工具失败或超限 → 兜底、任务 COMPLETED（票 06 §7）
- [x] trace DataPart 含 `tool_calls: [{round, name, args_summary, result_summary}]` + retrieval + timings
- [x] 接待 agent 的 system prompt 写入 `config/agents/reception.yaml: prompts.agent`，登记 `docs/prompts/reception.md`（受一致性校验）
- [x] 配置：`reception.yaml` 加 `agent:` 段（`max_steps`、`mcp_server_command`、`tool_timeout_seconds`），不硬编码
- [x] 离线测（票 10）：fake llm/broker/reply 测选工具/多轮/步数上限/失败兜底/工具证据进答复

## Comments

- 2026-10-08 落地（项目 `.venv`）。
- 文件：`common/llm_client.py`(+`chat_tools`)、`agents/reception/{react,mcp_client}.py`(新)、`service.py`(重写为 triage→ReAct→A2A 答复)、`app.py`(接 McpToolBroker)、`config/agents/reception.yaml`(+agent 段+prompts.agent)、`docs/prompts/reception.md`(+2 条)。
- 验证：`tests/unit/test_react_agent.py` **5 passed**；`tests/integration/test_reception_contract.py` **4 passed**（非客服不进 ReAct/答复、客服 ReAct 调 kb_search→答复、答复失败兜底、工具失败兜底、trace 含 tool_calls）；一致性 **18 条 OK**；全量默认 **50 passed, 2 deselected**。
- 拓扑落地：UI→接待(A2A)→ReAct(function-calling)→McpToolBroker→(stdio)MCP server→`kb_search`→(A2A)检索 Agent→Milvus RAG；收敛→接待 A2A 调答复 Agent。四层 + 三 Agent 全串。
- **移交工单 09**：真 MCP 子进程 + 真 qwen tool-calling + 真 Milvus 的端到端全链路 + 冒烟（离线测用的是 fake broker/llm）。
