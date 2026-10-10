"""进程内 A2A 驱动冒烟（工单 01）。

票 10 §6 的核心夹具：不绑端口、用 httpx ASGITransport 把 a2a-sdk 的 JSON-RPC 客户端
直连到进程内的 Starlette app，发一条消息，驱动占位 executor 跑到 COMPLETED，
并收到含占位回执文本的响应。工单 03/04/05 换成真实 executor 后，这条驱动路径不变。
"""

from __future__ import annotations

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest


@pytest.mark.parametrize("agent_name", ["reception", "retrieval", "reply"])
async def test_drive_inprocess_to_completed(agent_app_factory, agent_name):
    _cfg, app, sdk_card = agent_app_factory(agent_name)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http_client:
        client = await create_client(
            agent=sdk_card,
            client_config=ClientConfig(streaming=False, httpx_client=http_client),
        )
        collected: list[str] = []
        try:
            request = SendMessageRequest(
                message=new_text_message("ping", role=Role.ROLE_USER)
            )
            async for chunk in client.send_message(request):
                piece = get_stream_response_text(chunk)
                if piece:
                    collected.append(piece)
        finally:
            await client.close()

    joined = "".join(collected)
    assert f"[{agent_name}] placeholder received" in joined
