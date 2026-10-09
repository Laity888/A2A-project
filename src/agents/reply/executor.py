"""答复 Agent 的 AgentExecutor：解析入站 DataPart → 调 ReplyAgent → 出站 TextPart。

生命周期与 hello-world / 占位 executor 一致（建任务 → WORKING → artifact → COMPLETED）。
失败语义按票 06 §7：LLM 抛错 → 置 TASK_STATE_FAILED（由接待统一兜底）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from a2a.helpers import (
    get_data_parts,
    get_message_text,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from agents.reply.agent import ReplyAgent

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "response"


class ReplyAgentExecutor(AgentExecutor):
    def __init__(self, agent: ReplyAgent) -> None:
        self._agent = agent

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("generating answer"),
        )

        payload = self._extract_payload(context)
        total = 0
        started = False          # 是否已发出过该 artifact 的首个 chunk
        buffered: str | None = None  # 暂存一段，留到下一段/收尾再发，以正确标记 last_chunk
        response_id = str(uuid.uuid4())  # 流式各段共用同一 artifact_id，append 才有效
        try:
            async for delta in self._agent.generate_stream(payload):
                if not delta:
                    continue
                if buffered is not None:
                    await updater.add_artifact(
                        parts=[new_text_part(text=buffered, media_type="text/plain")],
                        artifact_id=response_id, name=ARTIFACT_NAME, append=started, last_chunk=False,
                    )
                    started = True
                    total += len(buffered)
                buffered = delta
        except Exception as exc:  # LLM 失败等 → FAILED，接待统一兜底（票 06 §7）
            logger.error("答复生成失败 err=%s", exc)
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message("answer generation failed"),
            )
            return

        # 收尾：最后一段带 last_chunk=True（无内容则发一个空终止块）
        final_text = buffered if buffered is not None else ""
        if buffered is not None:
            total += len(buffered)
        await updater.add_artifact(
            parts=[new_text_part(text=final_text, media_type="text/plain")],
            artifact_id=response_id, name=ARTIFACT_NAME, append=started, last_chunk=True,
        )
        logger.info("答复完成 task_id=%s 长度=%d", task.id, total)
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("answer completed"),
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError("Cancel is not supported.")

    @staticmethod
    def _extract_payload(context: RequestContext) -> dict[str, Any]:
        """优先取结构化 DataPart；退化：把纯文本当 query（便于手测）。"""
        message = context.message
        if not message:
            return {"query": "", "chunks": [], "history": []}
        data_parts = get_data_parts(list(message.parts))
        if data_parts and isinstance(data_parts[0], dict):
            return data_parts[0]
        text = get_message_text(message) or ""
        return {"query": text, "chunks": [], "history": []}
