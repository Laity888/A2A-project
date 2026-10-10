"""每角色 LLM 配置覆盖单测（工单：提速）。

覆盖 common.config.llm_for 的覆盖/类型强转语义，以及 reception/reply 新增 llm 段被正确读取，
和 LLMClient 对 max_tokens 的透传开关。纯配置/构造，不触网、不建真实连接池以外的调用。
"""

from __future__ import annotations

from common.config import llm_for, load_agent
from common.llm_client import LLMClient


def _cfg_with(**over):
    """用 reply 配置当载体，套一层可覆盖的 LLMConfig 造一个 AgentConfig 替身。"""
    from dataclasses import replace

    base = load_agent("reply")
    llm = replace(base.common.llm, **over)
    return replace(base, common=replace(base.common, llm=llm))


def test_llm_for_no_override_returns_base():
    cfg = load_agent("reply")
    assert llm_for(cfg) is cfg.common.llm


def test_llm_for_model_only_changes_model():
    cfg = load_agent("reply")
    out = llm_for(cfg, model="deepseek-v4-flash-0731")
    assert out.model == "deepseek-v4-flash-0731"
    assert out.temperature == cfg.common.llm.temperature
    assert out.timeout_seconds == cfg.common.llm.timeout_seconds


def test_llm_for_coerces_types():
    cfg = load_agent("reply")
    out = llm_for(cfg, timeout_seconds="20", max_retries="0", max_tokens="600")
    assert out.timeout_seconds == 20.0 and isinstance(out.timeout_seconds, float)
    assert out.max_retries == 0 and isinstance(out.max_retries, int)
    assert out.max_tokens == 600 and isinstance(out.max_tokens, int)


def test_llm_for_none_values_are_ignored():
    cfg = load_agent("reply")
    out = llm_for(cfg, model=None, max_tokens=None)
    assert out is cfg.common.llm


def test_reception_llm_section_loaded():
    s = load_agent("reception").settings.get("llm", {})
    assert s.get("model") == "deepseek-v4-flash-0731"
    assert int(s.get("triage_timeout_seconds")) == 20
    assert int(s.get("triage_max_retries")) == 0


def test_reply_llm_section_loaded():
    s = load_agent("reply").settings.get("llm", {})
    assert s.get("model") == "qwen3.8-max"
    assert int(s.get("max_tokens")) == 600


def test_llmclient_extra_kwargs():
    base = _cfg_with().common.llm
    c_no = LLMClient(base)  # max_tokens 默认 None
    assert c_no._extra_kwargs() == {}
    c_yes = LLMClient(_cfg_with(max_tokens=600).common.llm)
    assert c_yes._extra_kwargs() == {"max_tokens": 600}
