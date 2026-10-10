"""pytest 共享夹具（工单 01）。

- agent_app_factory：把某个 Agent 的配置装配成可进程内驱动的 Starlette app + SDK Agent Card
  （public_url 覆写成 http://testserver/ 以便 httpx ASGITransport 免端口直连）。
- fake_llm：可控的大模型桩，返回预置文本并记录调用（票 10 §2，单测不碰真模型）。

真实 executor 在工单 03/04/05 替换占位后，这两个夹具仍是测试底座。
"""

from __future__ import annotations

import dataclasses

import pytest

from common import a2a_app
from common import config as app_config
from common.placeholder_executor import PlaceholderAgentExecutor

# ASGITransport 免端口访问用的假基址（票 10 §6）
TEST_BASE_URL = "http://testserver/"


@pytest.fixture
def agent_app_factory():
    def _make(name: str):
        cfg = app_config.load_agent(name)
        card_cfg = dataclasses.replace(cfg.card, public_url=TEST_BASE_URL)
        app = a2a_app.build_app(PlaceholderAgentExecutor(name), card_cfg)
        sdk_card = a2a_app.to_agent_card(card_cfg)
        return cfg, app, sdk_card

    return _make


@pytest.fixture
def fake_llm():
    class FakeLLM:
        def __init__(self, reply: str = "fake-llm-reply") -> None:
            self.reply = reply
            self.calls: list[list[dict[str, str]]] = []

        async def chat(self, messages: list[dict[str, str]]) -> str:
            self.calls.append(messages)
            return self.reply

    return FakeLLM()
