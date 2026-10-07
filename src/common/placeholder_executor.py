"""占位 AgentExecutor（工单 01 脚手架用）。

只为证明「配置 → Agent Card → 路由 → 进程内可驱动」这条装配链是通的，
流程与 hello_world/agent_executor.py 一致：建任务 → WORKING → 产出 artifact → COMPLETED。
真实业务逻辑在工单 03（检索）/ 04（答复）/ 05（接待编排）里替换各自的 executor.py。
"""

from __future__ import annotations

import logging

from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "response"


class PlaceholderAgentExecutor(AgentExecutor):
    """回显式占位 executor，带 agent 名以区分三条装配链。"""

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("placeholder working"),
        )

        query = get_message_text(context.message) if context.message else ""
        logger.info("占位 executor 收到请求 agent=%s task_id=%s", self._agent_name, task.id)

        result = f"[{self._agent_name}] placeholder received: {query}"
        await updater.add_artifact(
            parts=[new_text_part(text=result, media_type="text/plain")],
            name=ARTIFACT_NAME,
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("placeholder completed"),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
