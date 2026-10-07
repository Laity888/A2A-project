"""A2A 服务端装配工厂（复用 hello_world/app.py 已跑通的装配模式，泛化成三 Agent 共用）。

给定一个 AgentExecutor + Agent Card 配置，产出 Starlette 应用：
Agent Card 发现路由 + JSON-RPC 服务路由。业务逻辑由各 Agent 的 executor 提供。
"""

from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from starlette.applications import Starlette
from starlette.routing import Route

from common.config import AgentCardConfig

logger = logging.getLogger(__name__)

# JSON-RPC 服务挂载路径（与 hello_world 一致，官方示例方式）
JSONRPC_PATH = "/"


def _to_skill(skill) -> AgentSkill:
    return AgentSkill(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        input_modes=skill.input_modes,
        output_modes=skill.output_modes,
        tags=skill.tags,
        examples=skill.examples,
    )


def to_agent_card(card_config: AgentCardConfig) -> AgentCard:
    """把配置里的 AgentCardConfig 转成 SDK 的 AgentCard（protobuf）。"""
    return AgentCard(
        name=card_config.name,
        description=card_config.description,
        version=card_config.version,
        default_input_modes=card_config.default_input_modes,
        default_output_modes=card_config.default_output_modes,
        capabilities=AgentCapabilities(streaming=card_config.streaming),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=card_config.protocol_binding,
                url=card_config.public_url,
                protocol_version=card_config.protocol_version,
            )
        ],
        skills=[_to_skill(s) for s in card_config.skills],
    )


def build_routes(executor: AgentExecutor, card_config: AgentCardConfig) -> list[Route]:
    public_card = to_agent_card(card_config)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=public_card,
    )
    routes: list[Route] = []
    routes.extend(create_agent_card_routes(public_card))
    routes.extend(create_jsonrpc_routes(request_handler, JSONRPC_PATH))
    return routes


def build_app(executor: AgentExecutor, card_config: AgentCardConfig) -> Starlette:
    """装配出一个可 uvicorn 运行、也可 httpx ASGITransport 进程内驱动的 Starlette app。"""
    routes = build_routes(executor, card_config)
    logger.info(
        "服务已装配 agent=%s public_url=%s skills=%s",
        card_config.name,
        card_config.public_url,
        [s.id for s in card_config.skills],
    )
    return Starlette(routes=routes)
