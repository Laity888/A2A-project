"""官方 Hello World Agent 的 Agent 本体：不调用 LLM，返回固定问候语。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class HelloWorldAgent:
    """Hello World Agent（对应官方示例的 HelloWorldAgent）。"""

    async def invoke(self, user_request: str) -> str:
        """生成回复。"""
        logger.info("echo 模式收到请求，长度=%d", len(user_request))
        return f"Hello, World! I have received your request ({user_request})"