"""接待 Agent 的下游「答复」A2A 客户端（工单 05/12，票 06 §1/§2）。

检索改由 MCP 工具 kb_search 承担后，接待这里只保留对答复 Agent 的 A2A 调用。
`reply_stream()` 以 streaming=True 连答复 Agent，逐段透传答复文本增量（工单：答复流式）；
`reply()` 保留为排空包装，向后兼容。`reply_stream` 只从 artifact 更新取正文，
绝不把 status_update 的 message 文本并入答复（否则 "generating answer" 会漏进气泡）。
离线测试注入 fake 顶替。透传同一个 `context_id`（票 06 §2 跨跳会话一致）。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import (
    get_artifact_text,
    new_data_message,
)
from a2a.types import Role, SendMessageRequest

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "response"


class ReplyClient:
    def __init__(self, cfg) -> None:
        orch = cfg.settings.get("orchestration", {})
        self._reply_url = orch["downstream_reply_url"]
        self._timeout = float(orch.get("client_timeout_seconds", 120))

    async def reply_stream(
        self, query: str, chunks: list[dict], history: list[dict],
        context_id: str | None = None, memory: dict | None = None, mode: str | None = None,
    ) -> AsyncIterator[str]:
        """流式调答复 Agent，逐段产出答复文本增量。memory=长期记忆/偏好上下文，mode=本轮意图(如 chitchat)。"""
        payload = {"query": query, "chunks": chunks, "history": history}
        if memory:
            payload["memory"] = memory
        if mode:
            payload["mode"] = mode
        async with httpx.AsyncClient(timeout=self._timeout) as hc:
            card = await A2ACardResolver(httpx_client=hc, base_url=self._reply_url).get_agent_card()
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=True, httpx_client=hc)
            )
            try:
                req = SendMessageRequest(
                    message=new_data_message(payload, role=Role.ROLE_USER, context_id=context_id)
                )
                streamed = False
                task_text: str | None = None  # 聚合 task 里的正文（SDK 可能不逐段而是末尾聚合）
                async for chunk in client.send_message(req):
                    if chunk.HasField("artifact_update"):
                        art = chunk.artifact_update.artifact
                        if getattr(art, "name", "") and art.name != ARTIFACT_NAME:
                            continue
                        piece = get_artifact_text(art)
                        if piece:
                            streamed = True
                            yield piece
                    elif chunk.HasField("task"):
                        for art in chunk.task.artifacts:
                            if getattr(art, "name", "") and art.name != ARTIFACT_NAME:
                                continue
                            piece = get_artifact_text(art)
                            if piece:
                                task_text = piece  # 记最后一段聚合结果
                # 逐段没产出但拿到聚合 task → 兜底发一次，避免答复为空 / 重复计数
                if not streamed and task_text:
                    yield task_text
            finally:
                await client.close()

    async def reply(
        self, query: str, chunks: list[dict], history: list[dict], context_id: str | None = None
    ) -> str:
        """非流式排空（向后兼容既有调用点）。"""
        parts = [d async for d in self.reply_stream(query, chunks, history, context_id=context_id)]
        return "".join(parts)
