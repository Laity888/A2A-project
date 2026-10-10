"""三 Agent 装配冒烟（工单 01）。

证明 config → Agent Card → 路由 这条竖切能跑通：用 httpx ASGITransport 进程内直连，
GET /.well-known/agent-card.json 返回 200 且卡片 name/skill id 与配置一致。
"""

from __future__ import annotations

import httpx
import pytest

CARD_PATH = "/.well-known/agent-card.json"


@pytest.mark.parametrize(
    "agent_name,expect_skill_id",
    [
        ("reception", "triage"),
        ("retrieval", "kb_retrieval"),
        ("reply", "answer"),
    ],
)
async def test_agent_card_endpoint_ok(agent_app_factory, agent_name, expect_skill_id):
    cfg, app, _card = agent_app_factory(agent_name)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get(CARD_PATH)

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == cfg.card.name
    skill_ids = [s["id"] for s in data["skills"]]
    assert expect_skill_id in skill_ids
