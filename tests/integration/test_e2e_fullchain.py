"""工单 09：全链路 e2e（真检索 Agent + 真 Milvus + 真 qwen）。默认被 addopts 排除，`-m e2e` 才跑。

前提：4 个服务在跑（retrieval:8002 / ui:8080 等），VM Milvus 可达，aliyun_API_KEY 已设。
服务不可达时自动 skip，不误红。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((REPO_ROOT / "tests/fixtures/retrieval_golden.json").read_text(encoding="utf-8"))
RETRIEVAL_URL = "http://127.0.0.1:8002"
UI_URL = "http://127.0.0.1:8080"


def _port_open(url: str) -> bool:
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((u.hostname, u.port)); return True
    except OSError:
        return False
    finally:
        s.close()


async def _retrieve(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as hc:
        card = await A2ACardResolver(httpx_client=hc, base_url=RETRIEVAL_URL).get_agent_card()
        client = await create_client(agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc))
        try:
            req = SendMessageRequest(message=new_data_message({"query": query, "top_k": 8}, role=Role.ROLE_USER))
            async for chunk in client.send_message(req):
                if chunk.HasField("task"):
                    for art in chunk.task.artifacts:
                        for d in get_data_parts(list(art.parts)):
                            if isinstance(d, dict) and "chunks" in d:
                                return d["chunks"]
            return []
        finally:
            await client.close()


@pytest.mark.e2e
async def test_retrieval_recall_at_2():
    if not _port_open(RETRIEVAL_URL):
        pytest.skip("检索 Agent 未运行（:8002），跳过 recall e2e")
    hits = 0
    for item in GOLDEN:
        chunks = await _retrieve(item["question"])
        urls = " ".join(c.get("source_url", "") for c in chunks[:2])
        if any(exp in urls for exp in item["expect"]):
            hits += 1
    recall = hits / len(GOLDEN)
    print(f"recall@2 = {hits}/{len(GOLDEN)} = {recall:.2f}")
    assert recall >= 0.8, f"recall@2={recall:.2f} < 0.8"


@pytest.mark.e2e
async def test_full_chain_happy_path():
    if not _port_open(UI_URL):
        pytest.skip("UI 未运行（:8080），跳过全链路 e2e")
    answer = ""
    async with httpx.AsyncClient(timeout=180) as c:
        async with c.stream("POST", f"{UI_URL}/api/chat",
                           json={"message": "如何获取 RAGFlow 的 API Key"}) as r:
            buf = ""
            async for ch in r.aiter_text():
                buf += ch
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    lines = frame.splitlines()
                    ev = next((l[6:].strip() for l in lines if l.startswith("event:")), "?")
                    dm = next((l[5:].strip() for l in lines if l.startswith("data:")), "{}")
                    if ev == "token":
                        answer += json.loads(dm or "{}").get("text", "")
    assert answer.strip(), "全链路应返回非空答复"
    assert "只能处理客服" not in answer, "服务问题不应走兜底"
