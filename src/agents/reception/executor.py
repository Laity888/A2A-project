"""接待 Agent 的 AgentExecutor（工单 05）。

把入站用户文本交给 ReceptionService 编排，产出最终答复 TextPart + trace DataPart。
状态：WORKING → COMPLETED（异常也 COMPLETED，兜底在服务层，票 06 §7）。
"""

from __future__ import annotations

import logging
import uuid

from a2a.helpers import (
    get_message_text,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "response"


def _extract_user_id(message) -> str | None:
    """从 A2A message.metadata 取 user_id（UI 登录态注入）；无则 None（匿名/回落）。"""
    if message is None:
        return None
    try:
        md = MessageToDict(message.metadata)
    except Exception:  # noqa: BLE001 - metadata 非 Struct/异常一律当无 user_id
        return None
    uid = md.get("user_id") if isinstance(md, dict) else None
    return str(uid) if uid else None


class ReceptionAgentExecutor(AgentExecutor):
    def __init__(self, service) -> None:
        self._service = service

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
            state=TaskState.TASK_STATE_WORKING, message=new_text_message("正在处理…")
        )

        text = get_message_text(context.message) if context.message else ""
        user_id = _extract_user_id(context.message)
        logger.info("接待收到请求 task_id=%s user_id=%s len=%d", task.id, user_id, len(text))

        answer_started = False  # 是否已发出答复正文的首个 artifact chunk
        response_id = str(uuid.uuid4())  # 流式各段共用同一 artifact_id，append 才有效
        async for ev in self._service.stream(text, task.context_id, user_id=user_id):
            kind = ev.get("ev")
            if kind == "status":
                await updater.update_status(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_text_message(str(ev.get("label", "processing"))),
                )
            elif kind == "token":
                await updater.add_artifact(
                    parts=[new_text_part(text=ev.get("text", ""), media_type="text/plain")],
                    artifact_id=response_id, name=ARTIFACT_NAME,
                    append=answer_started, last_chunk=False,
                )
                answer_started = True
            elif kind == "sources":
                await updater.add_artifact(
                    parts=[new_data_part(data=ev.get("data", {}), media_type="application/json")],
                    name="sources", append=False,
                )
            elif kind == "trace":
                await updater.add_artifact(
                    parts=[new_data_part(data=ev.get("data", {}), media_type="application/json")],
                    name="trace", append=False,
                )

        # 收尾：标记答复 artifact 的最后一块（仅当确有流式正文时）
        if answer_started:
            await updater.add_artifact(
                parts=[new_text_part(text="", media_type="text/plain")],
                artifact_id=response_id, name=ARTIFACT_NAME, append=True, last_chunk=True,
            )

        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED, message=new_text_message("done")
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
