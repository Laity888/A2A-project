"""检索 Agent 装配入口（工单 03）。

默认真实 Retriever（BGE-M3+hybrid+rerank）；测试可注入 fake retriever。
"""

from __future__ import annotations

from starlette.applications import Starlette

from agents.retrieval.executor import RetrievalExecutor
from agents.retrieval.retriever import Retriever
from common import a2a_app
from common.config import AgentConfig, load_agent


def build_app(config: AgentConfig | None = None, retriever=None) -> Starlette:
    cfg = config or load_agent("retrieval")
    if retriever is None:
        retriever = Retriever(cfg)
    return a2a_app.build_app(RetrievalExecutor(retriever), cfg.card)
