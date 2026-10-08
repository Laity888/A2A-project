"""kb_search 工具到「检索 A2A Agent」的桥：作为 A2A client 发 DataPart、取回 chunks。

MCP 工具在此把 A2A 服务适配成一个普通可调用对象；离线测试注入 fake，不连真检索。
"""

from __future__ import annotations

import logging

from mcp_server.config import load_mcp_config

logger = logging.getLogger(__name__)


def make_a2a_retrieval(url: str, timeout: float, top_k: int):
    """返回 async (query) -> {count, chunks:[...]}。"""

    async def _search(query: str, context_id: str = "") -> dict:
        import httpx
        from a2a.client import A2ACardResolver, ClientConfig, create_client
        from a2a.helpers import get_data_parts, new_data_message
        from a2a.types import Role, SendMessageRequest

        async with httpx.AsyncClient(timeout=timeout) as hc:
            card = await A2ACardResolver(httpx_client=hc, base_url=url).get_agent_card()
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc)
            )
            try:
                req = SendMessageRequest(
                    message=new_data_message(
                        {"query": query, "top_k": top_k},
                        role=Role.ROLE_USER,
                        context_id=context_id or None,
                    )
                )
                async for chunk in client.send_message(req):
                    if chunk.HasField("task"):
                        for art in chunk.task.artifacts:
                            for d in get_data_parts(list(art.parts)):
                                if isinstance(d, dict) and "chunks" in d:
                                    return {
                                        "count": d.get("count", len(d.get("chunks", []))),
                                        "chunks": d.get("chunks", []),
                                    }
                return {"count": 0, "chunks": []}
            finally:
                await client.close()

    return _search


def default_retrieval():
    cfg = load_mcp_config()
    return make_a2a_retrieval(
        cfg["retrieval_url"], float(cfg.get("client_timeout_seconds", 120)), int(cfg.get("top_k", 3))
    )
