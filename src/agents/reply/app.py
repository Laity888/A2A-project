"""答复 Agent 装配入口（工单 04）。

默认用真实 LLMClient 构建 ReplyAgent；测试可注入 fake agent（复用同一 executor + 进程内驱动路径）。
"""

from __future__ import annotations

from starlette.applications import Starlette

from agents.reply.agent import ReplyAgent
from agents.reply.executor import ReplyAgentExecutor
from common import a2a_app
from common.config import AgentConfig, llm_for, load_agent
from common.llm_client import LLMClient


def build_app(config: AgentConfig | None = None, agent: ReplyAgent | None = None) -> Starlette:
    cfg = config or load_agent("reply")
    if agent is None:
        llm_over = cfg.settings.get("llm", {}) or {}
        llm = LLMClient(llm_for(cfg, model=llm_over.get("model"), max_tokens=llm_over.get("max_tokens")))
        agent = ReplyAgent(llm, cfg.prompts)
    return a2a_app.build_app(ReplyAgentExecutor(agent), cfg.card)
