"""检索 Agent e2e（工单 03）：真实 Retriever（BGE-M3+rerank）对 VM Milvus 命中。

默认被 addopts 排除；跑法：
    ./.venv/Scripts/python.exe -m pytest -m e2e -s
前提：已 `python -m ingest run --reuse` 入库 + VM Milvus 可达 + 本地 bge-m3/reranker 权重。
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest

from agents.retrieval.executor import RetrievalExecutor
from agents.retrieval.retriever import Retriever
from common import a2a_app
from common.config import load_agent

TEST_BASE = "http://testserver/"


@pytest.mark.e2e
async def test_retrieval_e2e_real_milvus():
    cfg = load_agent("retrieval")
    retriever = Retriever(cfg)  # 加载 BGE-M3 + reranker + 连 Milvus
    card_cfg = dataclasses.replace(cfg.card, public_url=TEST_BASE)
    app = a2a_app.build_app(RetrievalExecutor(retriever), card_cfg)
    card = a2a_app.to_agent_card(card_cfg)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as hc:
        client = await create_client(
            agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc)
        )
        datas: list[dict] = []
        try:
            req = SendMessageRequest(
                message=new_data_message({"query": "如何部署 RAGFlow", "top_k": 3}, role=Role.ROLE_USER)
            )
            async for chunk in client.send_message(req):
                if chunk.HasField("task"):
                    for art in chunk.task.artifacts:
                        for d in get_data_parts(list(art.parts)):
                            if isinstance(d, dict) and "chunks" in d:
                                datas.append(d)
        finally:
            await client.close()

    assert datas, "应返回检索结果 DataPart"
    result = max(datas, key=lambda d: d.get("count", 0))
    assert result["count"] >= 1, f"真实库应命中，得 {result}"
    assert result["chunks"][0]["source_url"].startswith("https://ragflow.com.cn/")
    print("E2E 命中:", [(c["score"], c["source_url"]) for c in result["chunks"]])
