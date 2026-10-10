"""接待作为 MCP client 的工具代理（工单 12-B）。

用 mcp SDK 的 Client + StdioServerParameters 拉起独立的 `python -m mcp_server` 子进程，
把 MCP 工具转成 OpenAI function-calling 的 tools schema，并转发 call_tool。
ReActAgent 只依赖 list_tools/call 两个方法（ToolBroker 协议），便于离线用 fake 顶替。

- `context_id` 由接待注入到下游调用（票 06 §2 跨跳会话一致），并从 LLM 可见的工具
  schema 里隐藏，避免模型去编造它。
- 每次 call 用 `tool_timeout` 兜底（票 11）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import Any, Sequence

from mcp import Client, StdioServerParameters

logger = logging.getLogger(__name__)

_HIDDEN_PARAMS = ("context_id",)


class McpToolBroker:
    def __init__(self, *, command: str, args: Sequence[str], env: dict | None = None,
                 tool_timeout: float = 30.0) -> None:
        self._params = StdioServerParameters(command=command, args=list(args), env=env)
        self._timeout = float(tool_timeout)
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self._tools: list[dict] | None = None

    async def _ensure(self) -> Client:
        if self._client is None:
            self._stack = AsyncExitStack()
            self._client = await self._stack.enter_async_context(Client(self._params))
            logger.info("MCP client 已连接子进程 stdio server")
        return self._client

    @staticmethod
    def _hide_internal_params(parameters: dict) -> dict:
        if not isinstance(parameters, dict):
            return parameters
        props = parameters.get("properties")
        if isinstance(props, dict):
            for key in _HIDDEN_PARAMS:
                props.pop(key, None)
        req = parameters.get("required")
        if isinstance(req, list):
            parameters["required"] = [r for r in req if r not in _HIDDEN_PARAMS]
        return parameters

    async def list_tools(self) -> list[dict]:
        if self._tools is None:
            client = await self._ensure()
            res = await client.list_tools()
            self._tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": getattr(t, "description", "") or "",
                        "parameters": self._hide_internal_params(
                            getattr(t, "input_schema", None)
                            or {"type": "object", "properties": {}}
                        ),
                    },
                }
                for t in res.tools
            ]
        return self._tools

    async def call(self, name: str, args: dict, context_id: str | None = None) -> str:
        client = await self._ensure()
        payload: dict[str, Any] = dict(args or {})
        if context_id is not None:
            payload["context_id"] = context_id
        result = await asyncio.wait_for(client.call_tool(name, payload), timeout=self._timeout)
        texts = [c.text for c in (result.content or []) if getattr(c, "text", None)]
        if texts:
            return "\n".join(texts)
        structured = getattr(result, "structuredContent", None)
        return json.dumps(structured if structured is not None else {}, ensure_ascii=False)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._client = None
            self._tools = None
