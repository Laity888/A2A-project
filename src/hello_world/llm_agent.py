"""通义千问 Agent：通过 DashScope 的 OpenAI 兼容接口调用 qwen 模型。

与 HelloWorldAgent 保持同一个 invoke() 契约，方便在配置里切换。
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from hello_world.config import LLMConfig

logger = logging.getLogger(__name__)


class DashScopeAgent:
    """用 OpenAI 兼容协议调用通义千问。"""

    def __init__(self, llm_config: LLMConfig) -> None:
        self._config = llm_config
        self._client = AsyncOpenAI(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
            timeout=llm_config.timeout_seconds,
        )
        logger.info(
            "初始化通义千问客户端 provider=%s model=%s base_url=%s",
            llm_config.provider,
            llm_config.model,
            llm_config.base_url,
        )

    async def invoke(self, user_request: str) -> str:
        """把用户输入交给 qwen 模型，返回纯文本回复。"""
        logger.info("调用模型 model=%s", self._config.model)
        completion = await self._client.chat.completions.create(
            model=self._config.model,
            messages=[
                {"role": "system", "content": self._config.system_prompt},
                {"role": "user", "content": user_request},
            ],
            temperature=self._config.temperature,
        )
        content = completion.choices[0].message.content or ""
        logger.info(
            "模型返回完成 tokens=%s",
            getattr(completion.usage, "total_tokens", None),
        )
        return content

    async def close(self) -> None:
        await self._client.close()