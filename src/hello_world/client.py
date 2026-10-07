"""冒烟客户端：验证 Agent Card 端点与 JSON-RPC 端点是否连通。

用法：
    uv run python -m hello_world.client            # 用默认问题
    uv run python -m hello_world.client "你好"      # 自定义问题
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import display_agent_card, get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest

from hello_world.config import load_config
from hello_world.logging_setup import setup_logging

logger = logging.getLogger(__name__)


async def fetch_agent_card(base_url: str, timeout: httpx.Timeout):
    """走 /.well-known/agent-card.json 拉取 Agent Card。"""
    async with httpx.AsyncClient(timeout=timeout) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        return await resolver.get_agent_card()


async def send_message(
    card, text: str, *, streaming: bool, timeout: httpx.Timeout
) -> None:
    """调用 JSON-RPC 端点发一条消息。"""
    label = "streaming" if streaming else "non-streaming"
    logger.info("--- %s 调用 ---", label)

    async with httpx.AsyncClient(timeout=timeout) as httpx_client:
        client = await create_client(
            agent=card,
            client_config=ClientConfig(streaming=streaming, httpx_client=httpx_client),
        )
        try:
            request = SendMessageRequest(
                message=new_text_message(text, role=Role.ROLE_USER)
            )
            collected: list[str] = []
            async for chunk in client.send_message(request):
                piece = get_stream_response_text(chunk)
                if piece:
                    collected.append(piece)
                print(f"[{label}] {chunk}")
            print(f"[{label}] 文本汇总：{''.join(collected)}")
        finally:
            await client.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="A2A Hello World 冒烟测试")
    parser.add_argument("query", nargs="?", default="Hi there", help="要发送的问题")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config.server.log_level)
    timeout = httpx.Timeout(config.client.timeout_seconds)
    base_url = config.agent_card.public_url

    card = await fetch_agent_card(base_url, timeout)
    print("\n成功获取 Agent Card：")
    display_agent_card(card)

    await send_message(card, args.query, streaming=False, timeout=timeout)
    await send_message(card, args.query, streaming=True, timeout=timeout)


if __name__ == "__main__":
    asyncio.run(main())