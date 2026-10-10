"""ReAct 工具循环（工单 12-A；工单：改造成可流式发事件的异步生成器）。

对 ToolBroker 抽象编程（list_tools / call），LLM 通过 chat_tools 决定是否调用工具、
多轮迭代直到不再调用或达步数上限。产出收集到的工具证据 + 逐轮进度事件。
不含任何具体传输（MCP/A2A 由 broker 实现注入），便于离线测。

`run_stream` 逐段产出事件供上层（接待 service）翻成中间状态；`run` 保留为排空包装，
返回 {"evidence":[...]}，向后兼容既有单测与调用点。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class ReActAgent:
    def __init__(
        self, llm, broker, system_prompt: str, max_steps: int = 4, max_tool_calls: int | None = None
    ) -> None:
        self._llm = llm
        self._broker = broker
        self._system = system_prompt
        self._max_steps = int(max_steps)
        self._max_tool_calls = int(max_tool_calls) if max_tool_calls else None

    async def run_stream(
        self, user_text: str, history: list[dict], context_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 ReAct：产出 {type} 事件，type ∈ {tool_start, tool_done, converged, max_steps, result}。"""
        messages: list[dict] = [{"role": "system", "content": self._system}]
        for turn in history or []:
            role = turn.get("role")
            text = turn.get("text", "")
            if role in ("user", "assistant") and text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": user_text})

        tools = await self._broker.list_tools()
        evidence: list[dict] = []
        llm_ms = 0.0
        tool_ms = 0.0
        stop = False

        for step in range(self._max_steps):
            t0 = time.perf_counter()
            resp = await self._llm.chat_tools(messages, tools)
            llm_ms += (time.perf_counter() - t0) * 1000
            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                logger.info("ReAct 第 %d 轮无工具调用，收敛", step + 1)
                yield {"type": "converged", "round": step + 1}
                break

            messages.append({
                "role": "assistant",
                "content": resp.get("content") or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False)}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                if self._max_tool_calls is not None and len(evidence) >= self._max_tool_calls:
                    logger.warning("ReAct 达工具调用上限 %d，停止检索", self._max_tool_calls)
                    stop = True
                    break
                name = tc["name"]
                args = tc.get("args", {}) or {}
                yield {"type": "tool_start", "round": step + 1, "name": name, "args": args}
                t1 = time.perf_counter()
                result = await self._broker.call(name, args, context_id=context_id)
                tool_ms += (time.perf_counter() - t1) * 1000
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                item = {"round": step + 1, "name": name, "args": args, "result": result}
                evidence.append(item)
                logger.info("ReAct 第 %d 轮调用工具 %s", step + 1, name)
                yield {"type": "tool_done", **item}
            if stop:
                break
        else:
            logger.warning("ReAct 达步数上限 %d 仍未收敛", self._max_steps)
            yield {"type": "max_steps", "steps": self._max_steps}

        yield {
            "type": "result", "evidence": evidence,
            "llm_ms": round(llm_ms, 1), "tool_ms": round(tool_ms, 1), "n_calls": len(evidence),
        }

    async def run(
        self, user_text: str, history: list[dict], context_id: str | None = None
    ) -> dict[str, Any]:
        """排空 run_stream 取最终证据（向后兼容）。"""
        result: dict[str, Any] = {"evidence": []}
        async for ev in self.run_stream(user_text, history, context_id=context_id):
            if ev.get("type") == "result":
                result = {"evidence": ev.get("evidence", [])}
        return result
