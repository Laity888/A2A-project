"""真实接待流提供器：create_client(streaming=True) 连接待，产出规范化事件。

仅真实服务在线（工单 07/09）时使用；离线测试注入 fake provider_factory 顶替。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import ParseDict

from ui.translate import a2a_to_events

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "response"


def _with_user_id(message, user_id: str | None):
    """把 user_id 写进 A2A message.metadata（接待据此做多用户隔离）。无 user_id 原样返回。"""
    if user_id:
        ParseDict({"user_id": str(user_id)}, message.metadata)
    return message


class A2AReceptionProvider:
    def __init__(self, ui_cfg: dict[str, Any]) -> None:
        self._url = ui_cfg["reception_url"]
        self._timeout = float(ui_cfg.get("client_timeout_seconds", 120))

    async def stream(self, text: str, context_id: str, user_id: str | None = None) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as hc:
            card = await A2ACardResolver(httpx_client=hc, base_url=self._url).get_agent_card()
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=True, httpx_client=hc)
            )
            try:
                message = _with_user_id(
                    new_text_message(text, role=Role.ROLE_USER, context_id=context_id), user_id
                )
                req = SendMessageRequest(message=message)
                async for chunk in client.send_message(req):
                    for ev in a2a_to_events(chunk):
                        yield ev
                yield {"kind": "done"}
            except Exception as exc:  # noqa: BLE001
                logger.error("接待流调用失败 err=%s", exc)
                yield {"kind": "error", "message": str(exc)}
            finally:
                await client.close()
