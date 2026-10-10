"""配置加载冒烟测试（工单 01）。证明「不硬编码、参数都在 config、密钥走环境变量」。"""

from __future__ import annotations

import pytest

from common import config as C


def test_common_config_loads():
    c = C.load_common()
    assert c.llm.model
    assert c.llm.api_key_env == "aliyun_API_KEY"  # 密钥只从环境变量名读，不落配置值
    assert c.llm.timeout_seconds >= 120  # 票 05/11：大于冷启动
    assert c.infra.milvus.database == "a2a_cs_lzs"
    assert c.infra.milvus.collection == "cs_kb_lzs"


def test_reception_config():
    r = C.load_agent("reception")
    assert r.server.port == 8001
    assert r.card.skills[0].id == "triage"
    assert r.card.streaming is True
    assert "fallback_reply" in r.prompts
    assert r.settings["session"]["max_history_rounds"] > 0


def test_retrieval_config():
    v = C.load_agent("retrieval")
    assert v.server.port == 8002
    assert v.settings["retrieval"]["top_k"] >= 3  # 召回深度（工单09 因语料增大调到 8）
    assert v.settings["retrieval"]["rerank_n"] == 2
    assert v.settings["performance"]["infer_concurrency"] == 1  # 票 11 防 OOM 主闸门
    assert v.settings["performance"]["warmup"]["enabled"] is True
    assert v.card.default_input_modes == ["application/json"]


def test_reply_config():
    p = C.load_agent("reply")
    assert p.server.port == 8003
    assert p.card.skills[0].id == "answer"
    assert p.card.default_output_modes == ["text/plain"]


def test_unknown_agent_raises():
    with pytest.raises(C.ConfigError):
        C.load_agent("ghost")
