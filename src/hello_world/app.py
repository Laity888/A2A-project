"""把配置装配成 A2A 服务端：Agent Card -> Executor -> RequestHandler -> Starlette 路由。"""

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

from hello_world.agent import HelloWorldAgent
from hello_world.agent_executor import HelloWorldAgentExecutor
from hello_world.config import (
    EXECUTOR_MODE_LLM,
    AgentCardConfig,
    AppConfig,
    SkillConfig,
)
from hello_world.llm_agent import DashScopeAgent

logger = logging.getLogger(__name__)


def _to_skill(skill: SkillConfig) -> AgentSkill:
    return AgentSkill(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        input_modes=skill.input_modes,
        output_modes=skill.output_modes,
        tags=skill.tags,
        examples=skill.examples,
    )


def build_agent_card(card_config: AgentCardConfig, *, extended: bool) -> AgentCard:
    """构造 Agent Card。extended=True 时返回认证后的扩展卡片。"""
    source = card_config.extended if extended else card_config
    skills = [_to_skill(s) for s in source.skills]
    return AgentCard(
        name=source.name,
        description=source.description,
        version=source.version,
        default_input_modes=card_config.default_input_modes,
        default_output_modes=card_config.default_output_modes,
        capabilities=AgentCapabilities(
            streaming=card_config.streaming,
            extended_agent_card=card_config.extended.enabled,
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=card_config.protocol_binding,
                url=card_config.public_url,
                protocol_version=card_config.protocol_version,
            )
        ],
        skills=skills,
    )


def build_agent(config: AppConfig) -> HelloWorldAgent | DashScopeAgent:
    """按配置选择 Agent 实现。"""
    if config.executor_mode == EXECUTOR_MODE_LLM:
        return DashScopeAgent(config.llm)
    return HelloWorldAgent()


def build_executor(config: AppConfig) -> AgentExecutor:
    return HelloWorldAgentExecutor(agent=build_agent(config))


def build_routes(config: AppConfig) -> list[Route]:
    """按官方示例的方式拼出两类路由：Agent Card 发现 + JSON-RPC 服务。"""
    public_card = build_agent_card(config.agent_card, extended=False)
    request_handler = DefaultRequestHandler(
        agent_executor=build_executor(config),
        task_store=InMemoryTaskStore(),
        agent_card=public_card,
        extended_agent_card=(
            build_agent_card(config.agent_card, extended=True)
            if config.agent_card.extended.enabled
            else None
        ),
    )

    routes: list[Route] = []
    routes.extend(create_agent_card_routes(public_card))
    routes.extend(create_jsonrpc_routes(request_handler, "/"))
    return routes


def build_app(config: AppConfig) -> Starlette:
    routes = build_routes(config)
    logger.info(
        "服务已装配 mode=%s card_url=%s public_url=%s",
        config.executor_mode,
        "/.well-known/agent-card.json",
        config.agent_card.public_url,
    )
    return Starlette(routes=routes)