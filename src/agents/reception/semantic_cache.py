"""接待作为语义缓存（第二层）客户端：经 A2A 调检索 Agent 的 query_cache 读写。

接待进程不 import Milvus / 不加载 embedding 模型——所有向量读写委托给检索 Agent
（唯一持有 BGE-M3 + Milvus 连接的进程）。这里只负责发 {action,query,answer,user_id}
的 DataPart、解析回包，并把任何异常降级为「未命中 / 写失败忽略」：缓存是优化，绝不致命。

- lookup(text, user_id) -> str | None：命中返回缓存答案，否则 None。
- record(text, answer, user_id) -> None：尽力写回，失败静默。
- 离线测试可注入 fake（实现同名两方法即可）。

约定：每次调用新建独立 httpx.AsyncClient（create_client 会复用并在 close 时关掉传入的
client，循环里复用会报 “client has been closed”）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest

logger = logging.getLogger(__name__)


class A2ASemanticCache:
    def __init__(self, *, url: str, timeout: float = 15.0) -> None:
        self._url = url
        self._timeout = float(timeout)

    async def _call(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self._timeout) as hc:
            card = await A2ACardResolver(httpx_client=hc, base_url=self._url).get_agent_card()
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc)
            )
            try:
                req = SendMessageRequest(
                    message=new_data_message(payload, role=Role.ROLE_USER)
                )
                async for chunk in client.send_message(req):
                    if chunk.HasField("task"):
                        for art in chunk.task.artifacts:
                            for d in get_data_parts(list(art.parts)):
                                if isinstance(d, dict) and d.get("action"):
                                    return d
                return None
            finally:
                await client.close()

    async def lookup(self, text: str, user_id: str | None = None, pref_sig: str = "") -> str | None:
        try:
            d = await self._call({
                "action": "cache_lookup", "query": text, "user_id": user_id or "anon",
                "pref_sig": pref_sig or "",
            })
            if d and d.get("hit"):
                answer = d.get("answer")
                if answer:
                    logger.info("语义缓存命中 user_id=%s score=%s", user_id, d.get("score"))
                    return answer
        except Exception as exc:  # noqa: BLE001 - 缓存读失败一律当未命中，走正常链路
            logger.warning("语义缓存 lookup 失败（忽略）err=%s", exc)
        return None

    async def record(self, text: str, answer: str, user_id: str | None = None, pref_sig: str = "") -> None:
        try:
            await self._call({
                "action": "cache_write", "query": text, "answer": answer,
                "user_id": user_id or "anon", "pref_sig": pref_sig or "",
            })
        except Exception as exc:  # noqa: BLE001 - 写失败静默，不影响本次答复
            logger.warning("语义缓存 record 失败（忽略）err=%s", exc)
