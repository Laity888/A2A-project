"""接待 Agent 装配入口（工单 05 起，工单 12 接入 ReAct + MCP 工具）。

默认真实依赖：LLMClient + TriageAgent + McpToolBroker(拉起 stdio mcp_server) + A2AHub(答复) + SessionStore；
测试可注入 fake（triage / tool_broker / reply_client / session / llm）。
"""

from __future__ import annotations

import os
import sys

from starlette.applications import Starlette

from agents.reception.answer_cache import AnswerCache
from agents.reception.executor import ReceptionAgentExecutor
from agents.reception.hub import ReplyClient
from agents.reception.mcp_client import McpToolBroker
from agents.reception.memory import make_memory_store
from agents.reception.semantic_cache import A2ASemanticCache
from agents.reception.service import ReceptionService
from agents.reception.session import make_session_store
from agents.reception.triage import TriageAgent
from common import a2a_app
from common.config import AgentConfig, llm_for, load_agent
from common.llm_client import LLMClient

DEFAULT_FALLBACK = "抱歉，我目前只能处理客服/工单相关的问题。"
DEFAULT_AGENT_PROMPT = "你是客服调度 agent，可调用工具检索知识库与处理工单/订单。"


def _make_broker(cfg: AgentConfig) -> McpToolBroker:
    agent_cfg = cfg.settings.get("agent", {})
    command = agent_cfg.get("mcp_server_command", ["-m", "mcp_server"])
    env = dict(os.environ)
    env["PYTHONPATH"] = "src" + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return McpToolBroker(
        command=sys.executable, args=command, env=env,
        tool_timeout=float(agent_cfg.get("tool_timeout_seconds", 30)),
    )


def build_app(
    config: AgentConfig | None = None, *, llm=None, triage=None,
    tool_broker=None, reply_client=None, session=None, semantic_cache=None, memory=None,
) -> Starlette:
    cfg = config or load_agent("reception")
    llm_over = cfg.settings.get("llm", {}) or {}
    if llm is None:
        # ReAct 路由：快模型（支持 function-calling）
        llm = LLMClient(llm_for(cfg, model=llm_over.get("model")))
    if triage is None:
        # 分流：同一快模型 + 短超时不重试（慢响应快速失败→保守放行，堵住偶发超时）
        triage_llm = LLMClient(llm_for(
            cfg,
            model=llm_over.get("model"),
            timeout_seconds=llm_over.get("triage_timeout_seconds"),
            max_retries=llm_over.get("triage_max_retries"),
        ))
        triage = TriageAgent(triage_llm, cfg.prompts)
    if tool_broker is None:
        tool_broker = _make_broker(cfg)
    if reply_client is None:
        reply_client = ReplyClient(cfg)
    if session is None:
        session = make_session_store(cfg)

    agent_cfg = cfg.settings.get("agent", {})
    mtc = agent_cfg.get("max_tool_calls")
    ac = cfg.settings.get("answer_cache", {}) or {}
    answer_cache = AnswerCache(
        enabled=bool(ac.get("enabled", True)),
        ttl_seconds=float(ac.get("ttl_seconds", 86400)),
        normalize=bool(ac.get("normalize", True)),
        max_entries=int(ac.get("max_entries", 512)),
        scope=ac.get("scope", "user"),
    )
    # 语义缓存（第二层）：经 A2A 调检索 Agent 的 query_cache，接待不碰 Milvus
    if semantic_cache is None:
        sc = cfg.settings.get("semantic_cache", {}) or {}
        retrieval_url = (cfg.settings.get("orchestration", {}) or {}).get("downstream_retrieval_url")
        if sc.get("enabled") and retrieval_url:
            semantic_cache = A2ASemanticCache(
                url=retrieval_url, timeout=float(sc.get("timeout_seconds", 15))
            )
    # 长期记忆 & 用户偏好：MySQL 落库；禁用/连不上 → None（功能关闭，不影响主链路）
    if memory is None:
        memory = make_memory_store(cfg)
    service = ReceptionService(
        llm=llm,
        triage=triage,
        tool_broker=tool_broker,
        reply_client=reply_client,
        session=session,
        agent_prompt=(cfg.prompts.get("agent") or DEFAULT_AGENT_PROMPT).strip(),
        fallback_text=(cfg.prompts.get("fallback_reply") or DEFAULT_FALLBACK).strip(),
        max_steps=int(agent_cfg.get("max_steps", 4)),
        max_tool_calls=(int(mtc) if mtc else None),
        answer_cache=answer_cache,
        semantic_cache=semantic_cache,
        memory=memory,
    )
    return a2a_app.build_app(ReceptionAgentExecutor(service), cfg.card)
