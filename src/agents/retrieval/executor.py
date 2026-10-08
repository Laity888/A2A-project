"""检索 Agent 的 AgentExecutor（工单 03）。

入站 DataPart `{query, top_k}` → 调 retriever → 出站 Artifact：
一个 DataPart `{query, count, chunks:[{text,source,source_url,category,score}]}` + 一个摘要 TextPart。
空结果 → COMPLETED + chunks:[]（空≠失败，票 06 §7）；retriever 抛错 → TASK_STATE_FAILED（交接待兜底）。
retriever 由构造注入（真实 = BGE-M3+hybrid+rerank，见 retriever.py；测试注入 fake）。
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "retrieval"


class RetrievalExecutor(AgentExecutor):
    def __init__(self, retriever) -> None:
        self._retriever = retriever

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING, message=new_text_message("retrieving")
        )

        payload = self._extract_payload(context)
        action = str(payload.get("action", "retrieve"))

        if action == "cache_lookup":
            await self._handle_cache_lookup(updater, payload)
            return
        if action == "cache_write":
            await self._handle_cache_write(updater, payload)
            return

        await self._handle_retrieve(updater, payload)

    # ---- 知识库检索（原有语义）----
    async def _handle_retrieve(self, updater: TaskUpdater, payload: dict[str, Any]) -> None:
        query = payload.get("query", "") or ""
        top_k = payload.get("top_k")
        try:
            chunks = await self._retriever.retrieve(query, top_k)
        except Exception as exc:  # noqa: BLE001 - 检索失败 → FAILED，接待兜底
            logger.error("检索失败 query=%r err=%s", query, exc)
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message("retrieval failed"),
            )
            return

        chunks = list(chunks or [])
        result: dict[str, Any] = {"query": query, "count": len(chunks), "chunks": chunks}
        logger.info("检索完成 query=%r 命中=%d", query, len(chunks))
        await updater.add_artifact(
            parts=[
                new_data_part(data=result, media_type="application/json"),
                new_text_part(
                    text=f"检索到 {len(chunks)} 条相关片段。", media_type="text/plain"
                ),
            ],
            name=ARTIFACT_NAME,
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED, message=new_text_message("retrieval done")
        )

    # ---- 语义缓存读（第二层，尽力而为）----
    async def _handle_cache_lookup(self, updater: TaskUpdater, payload: dict[str, Any]) -> None:
        query = payload.get("query", "") or ""
        user_id = payload.get("user_id")
        pref_sig = payload.get("pref_sig") or ""
        hit: dict[str, Any] | None = None
        try:
            hit = await self._retriever.cache_lookup(query, user_id, pref_sig)
        except Exception as exc:  # noqa: BLE001 - 缓存是优化，绝不 FAILED
            logger.warning("语义缓存查询失败（退回未命中）query=%r err=%s", query, exc)
        result = {
            "action": "cache_lookup",
            "hit": bool(hit),
            "answer": (hit or {}).get("answer"),
            "score": (hit or {}).get("score"),
        }
        await updater.add_artifact(
            parts=[new_data_part(data=result, media_type="application/json")],
            name=ARTIFACT_NAME,
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED, message=new_text_message("cache lookup done")
        )

    # ---- 语义缓存写（第二层，尽力而为）----
    async def _handle_cache_write(self, updater: TaskUpdater, payload: dict[str, Any]) -> None:
        query = payload.get("query", "") or ""
        answer = payload.get("answer", "") or ""
        user_id = payload.get("user_id")
        pref_sig = payload.get("pref_sig") or ""
        written = False
        try:
            await self._retriever.cache_write(query, answer, user_id, pref_sig)
            written = True
        except Exception as exc:  # noqa: BLE001 - 写失败不影响调用方，仍 COMPLETED
            logger.warning("语义缓存写入失败（忽略）query=%r err=%s", query, exc)
        await updater.add_artifact(
            parts=[new_data_part(data={"action": "cache_write", "written": written}, media_type="application/json")],
            name=ARTIFACT_NAME,
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED, message=new_text_message("cache write done")
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")

    @staticmethod
    def _extract_payload(context: RequestContext) -> dict[str, Any]:
        message = context.message
        if not message:
            return {}
        data_parts = get_data_parts(list(message.parts))
        if data_parts and isinstance(data_parts[0], dict):
            return data_parts[0]
        return {}
