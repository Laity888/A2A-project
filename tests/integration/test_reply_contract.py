"""答复 Agent 契约测试（工单 04）。

分三层：
- 纯单测 build_reply_messages：验证 system + history + chunks(含出处) + query 的组装（票 09/06）。
- 进程内驱动（ASGITransport + create_client）：发 DataPart {query,chunks,history} → 断言产出
  TextPart 答复、任务到 COMPLETED（票 10 §2/§6，用 fake agent 不碰真模型）。
- 失败分支：agent 抛错 → 任务置 TASK_STATE_FAILED（票 06 §7）。
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_data_message
from a2a.types import Role, SendMessageRequest, TaskState

from agents.reply.agent import build_reply_messages
from agents.reply.executor import ReplyAgentExecutor
from common import a2a_app
from common.config import load_agent

TEST_BASE = "http://testserver/"
CARD_STATE_COMPLETED = int(TaskState.TASK_STATE_COMPLETED)
CARD_STATE_FAILED = int(TaskState.TASK_STATE_FAILED)


class FakeReplyAgent:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.payloads: list[dict] = []

    async def generate(self, payload: dict) -> str:
        self.payloads.append(payload)
        return self.reply

    async def generate_stream(self, payload: dict):
        self.payloads.append(payload)
        yield self.reply


class BoomAgent:
    async def generate(self, payload: dict) -> str:  # noqa: ARG002
        raise RuntimeError("llm down")

    async def generate_stream(self, payload: dict):  # noqa: ARG002
        raise RuntimeError("llm down")
        yield  # pragma: no cover - 使其成为 async generator


def _make_reply_app(agent):
    cfg = load_agent("reply")
    card_cfg = dataclasses.replace(cfg.card, public_url=TEST_BASE)
    app = a2a_app.build_app(ReplyAgentExecutor(agent), card_cfg)
    return app, a2a_app.to_agent_card(card_cfg)


async def _send_data(app, card, payload: dict) -> tuple[str, list[int]]:
    texts: list[str] = []
    states: list[int] = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as hc:
        client = await create_client(
            agent=card, client_config=ClientConfig(streaming=False, httpx_client=hc)
        )
        try:
            request = SendMessageRequest(
                message=new_data_message(payload, role=Role.ROLE_USER)
            )
            async for chunk in client.send_message(request):
                piece = get_stream_response_text(chunk)
                if piece:
                    texts.append(piece)
                if chunk.HasField("task"):
                    states.append(int(chunk.task.status.state))
                elif chunk.HasField("status_update"):
                    states.append(int(chunk.status_update.status.state))
        finally:
            await client.close()
    return "".join(texts), states


def test_build_reply_messages_assembles_all_parts():
    payload = {
        "query": "如何退货",
        "chunks": [
            {"text": "7天可退", "source_url": "http://s/a"},
            {"text": "联系客服", "source_url": "http://s/b"},
        ],
        "history": [
            {"role": "user", "text": "我买了杯子"},
            {"role": "assistant", "text": "您好"},
        ],
    }
    msgs = build_reply_messages(payload, "你是中文客服。")

    assert msgs[0] == {"role": "system", "content": "你是中文客服。"}
    assert {"role": "user", "content": "我买了杯子"} in msgs
    assert {"role": "assistant", "content": "您好"} in msgs
    assert msgs[-1]["role"] == "user"
    assert "如何退货" in msgs[-1]["content"]
    assert "7天可退" in msgs[-1]["content"] and "http://s/a" in msgs[-1]["content"]


def test_build_reply_messages_no_chunks_still_works():
    msgs = build_reply_messages({"query": "hi", "chunks": [], "history": []}, "S")
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user" and "hi" in msgs[-1]["content"]


async def test_reply_happy_path_returns_text_and_completes():
    agent = FakeReplyAgent("杯子碎了可为您办理退货。")
    app, card = _make_reply_app(agent)
    payload = {
        "query": "杯子碎了",
        "chunks": [{"text": "支持7天退货", "source_url": "http://x/1"}],
        "history": [],
    }
    text, states = await _send_data(app, card, payload)

    assert agent.payloads and agent.payloads[0]["query"] == "杯子碎了"
    assert "杯子碎了可为您办理退货。" in text
    assert states and states[-1] == CARD_STATE_COMPLETED


async def test_reply_llm_failure_sets_failed_state():
    app, card = _make_reply_app(BoomAgent())
    payload = {"query": "x", "chunks": [], "history": []}
    _text, states = await _send_data(app, card, payload)

    assert states and states[-1] == CARD_STATE_FAILED


@pytest.mark.llm
@pytest.mark.e2e
async def test_reply_real_llm_smoke():
    """真 LLM e2e（需 aliyun_API_KEY，默认被 addopts 排除，-m llm 才跑）。"""
    from agents.reply.agent import ReplyAgent
    from common.config import load_agent
    from common.llm_client import LLMClient

    cfg = load_agent("reply")
    if not cfg.common.llm.api_key:
        pytest.skip("未设置环境变量 aliyun_API_KEY，跳过真实 LLM e2e")

    agent = ReplyAgent(LLMClient(cfg.common.llm), cfg.prompts)
    app, card = _make_reply_app(agent)
    payload = {
        "query": "签收后还能退货吗？",
        "chunks": [
            {"text": "支持签收后7天内无理由退货。", "source_url": "http://ragflow/docs/return"}
        ],
        "history": [],
    }
    text, states = await _send_data(app, card, payload)

    assert text.strip(), "真 LLM 应产出非空答复"
    assert states and states[-1] == CARD_STATE_COMPLETED
