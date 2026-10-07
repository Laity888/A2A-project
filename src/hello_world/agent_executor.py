"""AgentExecutor：把 A2A 请求转成对 Agent 的调用，并把结果写回事件队列。

流程与官方示例一致：建任务 -> 置 WORKING -> 调 Agent -> 产出 artifact -> 置 COMPLETED。
Agent 通过构造函数注入，因此 echo / llm 两种模式共用同一份执行逻辑。
"""

from __future__ import annotations

import logging

from typing import Protocol

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


class InvokableAgent(Protocol):
    """Agent 契约：给定用户输入，返回文本回复。"""

    async def invoke(self, user_request: str) -> str: ...


class HelloWorldAgentExecutor(AgentExecutor):
    """Hello World 的 AgentExecutor 实现。"""

    def __init__(self, agent: InvokableAgent) -> None:
        self.agent = agent

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """处理一次用户请求。"""
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Processing request..."),
        )

        query = get_message_text(context.message) if context.message else ""
        logger.info("收到请求 task_id=%s", task.id)

        if query:
            result = await self.agent.invoke(user_request=query)
        else:
            result = "No text input is provided!"

        await task_updater.add_artifact(
            parts=[new_text_part(text=result, media_type="text/plain")],
            name=ARTIFACT_NAME,
        )
        logger.info("回复完成 task_id=%s 长度=%d", task.id, len(result))

        await task_updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("Request is completed!"),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """官方示例不支持取消。"""
        raise NotImplementedError("Cancel is not supported.")