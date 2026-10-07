"""LLM 客户端：DashScope OpenAI 兼容接口，带超时与指数退避重试（票 11 §4）。

不自建限流（靠接待对下游非流式串行压并发）；重试只兜 429/瞬时错误。
参数全部来自 CommonConfig.llm，不硬编码；API Key 从环境变量读（票 05 硬约束）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from openai import AsyncOpenAI, APIStatusError, APITimeoutError, RateLimitError

from common.config import LLMConfig

logger = logging.getLogger(__name__)

# 可重试的状态码：限流与服务端瞬时错误
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """重试耗尽仍失败时抛出，交由上层（接待）走兜底话术（票 06 §7）。"""


class LLMClient:
    """通义千问（DashScope OpenAI 兼容）客户端。"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )
        logger.info(
            "初始化 LLM 客户端 provider=%s model=%s retries=%d",
            config.provider,
            config.model,
            config.max_retries,
        )

    def _extra_kwargs(self) -> dict:
        """max_tokens 设了才透传（限制答复长度提速）；None 时不加，保持默认行为。"""
        return {"max_tokens": self._config.max_tokens} if self._config.max_tokens else {}

    async def _retry_call(self, op: str, invoke):
        """共享重试环：调 `invoke()`，可重试错误按 2^attempt 退避重发；重试用尽或不可重试抛 LLMError。

        `op` 只出现在日志（warn/error）里，用于区分是哪个调用点（"调用"/"工具调用"）；
        LLMError 消息文本统一为 `LLM 调用失败：<err>`（保持既有措辞，不动上游兜底文案）。

        `chat_stream` 不走此 helper：它的重发门槛含 `started`（一旦吐字即无法回退，只能直接抛），
        且 try 块是 async generator 逐段 yield，套 helper 反而更绕。
        """
        attempts = self._config.max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await invoke()
            except (RateLimitError, APITimeoutError, APIStatusError) as exc:
                last_exc = exc
                if not self._is_retryable(exc) or attempt >= attempts - 1:
                    break
                backoff = 2**attempt  # 1s, 2s, 4s ...
                logger.warning(
                    "模型 %s 失败将重试 attempt=%d/%d backoff=%ds err=%s",
                    op, attempt + 1, attempts, backoff, exc,
                )
                await asyncio.sleep(backoff)
        logger.error("模型 %s 最终失败 err=%s", op, last_exc)
        raise LLMError(f"LLM 调用失败：{last_exc}") from last_exc

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """发送一次对话请求，失败按指数退避重试；耗尽仍失败抛 LLMError。"""
        async def _invoke():
            completion = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                **self._extra_kwargs(),
            )
            logger.info(
                "模型返回成功 tokens=%s",
                getattr(completion.usage, "total_tokens", None),
            )
            return completion

        completion = await self._retry_call("调用", _invoke)
        return completion.choices[0].message.content or ""

    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        """流式对话：逐段产出 delta 文本（工单：答复流式）。

        只在「尚未吐出任何 token」时对可重试错误退避重发——流一旦开始就无法回退，
        故吐字后若再出错直接抛 LLMError，交由上层（接待）走兜底（票 06 §7）。
        """
        attempts = self._config.max_retries + 1
        last_exc: Exception | None = None
        started = False
        for attempt in range(attempts):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._config.model,
                    messages=messages,
                    temperature=self._config.temperature,
                    stream=True,
                    **self._extra_kwargs(),
                )
                async for event in stream:
                    choices = getattr(event, "choices", None)
                    if not choices:
                        continue
                    delta = getattr(choices[0].delta, "content", None)
                    if delta:
                        started = True
                        yield delta
                logger.info("模型流式返回成功 attempt=%d", attempt + 1)
                return
            except (RateLimitError, APITimeoutError, APIStatusError) as exc:
                last_exc = exc
                # 已吐字 / 不可重试 / 重试用尽 → 不再重发
                if started or not self._is_retryable(exc) or attempt >= attempts - 1:
                    break
                backoff = 2**attempt
                logger.warning(
                    "流式调用失败将重发 attempt=%d/%d backoff=%ds err=%s",
                    attempt + 1, attempts, backoff, exc,
                )
                await asyncio.sleep(backoff)
        logger.error("模型流式调用最终失败 err=%s", last_exc)
        raise LLMError(f"LLM 流式调用失败：{last_exc}") from last_exc

    async def chat_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        """带 function-calling 的对话：返回 {content, tool_calls:[{id,name,args}]}，失败退避重试。"""
        async def _invoke():
            return await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                tools=tools,
                **self._extra_kwargs(),
            )

        completion = await self._retry_call("工具调用", _invoke)
        msg = completion.choices[0].message
        tool_calls: list[dict] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (ValueError, TypeError):
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "args": args})
        logger.info("chat_tools 返回 tool_calls=%d", len(tool_calls))
        return {"content": msg.content or "", "tool_calls": tool_calls}

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (RateLimitError, APITimeoutError)):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code in _RETRYABLE_STATUS
        return False

    async def close(self) -> None:
        await self._client.close()
