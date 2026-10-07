"""配置加载：从 config/*.yaml 与环境变量装配出强类型配置对象。

规则：
- 任何可变参数都来自配置文件，代码里不硬编码。
- 密钥类信息只从环境变量读取（.env 由 python-dotenv 加载）。
- 找不到必需项时直接报错并说清怎么补，不做静默兜底。
"""

from __future__ import annotations

import os

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "hello_world.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

EXECUTOR_MODE_ECHO = "echo"
EXECUTOR_MODE_LLM = "llm"
VALID_EXECUTOR_MODES = (EXECUTOR_MODE_ECHO, EXECUTOR_MODE_LLM)


class ConfigError(RuntimeError):
    """配置缺失或非法时抛出，消息里带上补救办法。"""


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    log_level: str


@dataclass(frozen=True)
class SkillConfig:
    id: str
    name: str
    description: str
    input_modes: list[str] = field(default_factory=list)
    output_modes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExtendedCardConfig:
    enabled: bool
    name: str
    description: str
    version: str
    skills: list[SkillConfig] = field(default_factory=list)


@dataclass(frozen=True)
class AgentCardConfig:
    name: str
    description: str
    version: str
    public_url: str
    protocol_binding: str
    protocol_version: str
    default_input_modes: list[str]
    default_output_modes: list[str]
    streaming: bool
    skills: list[SkillConfig]
    extended: ExtendedCardConfig


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    api_key_env: str
    api_key: str
    temperature: float
    timeout_seconds: float
    system_prompt: str


@dataclass(frozen=True)
class ClientSettings:
    """冒烟客户端设置。"""

    timeout_seconds: float


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    agent_card: AgentCardConfig
    executor_mode: str
    llm: LLMConfig
    client: ClientSettings
    config_path: Path


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"配置文件缺少 {where}.{key}")
    return mapping[key]


def _parse_skill(raw: dict[str, Any], where: str) -> SkillConfig:
    return SkillConfig(
        id=_require(raw, "id", where),
        name=_require(raw, "name", where),
        description=_require(raw, "description", where),
        input_modes=list(raw.get("input_modes", [])),
        output_modes=list(raw.get("output_modes", [])),
        tags=list(raw.get("tags", [])),
        examples=list(raw.get("examples", [])),
    )


def _parse_agent_card(raw: dict[str, Any]) -> AgentCardConfig:
    extended_raw = raw.get("extended") or {}
    extended = ExtendedCardConfig(
        enabled=bool(extended_raw.get("enabled", False)),
        name=extended_raw.get("name", raw["name"]),
        description=extended_raw.get("description", raw["description"]),
        version=extended_raw.get("version", raw["version"]),
        skills=[
            _parse_skill(s, "agent_card.extended.skills")
            for s in extended_raw.get("skills", [])
        ],
    )
    return AgentCardConfig(
        name=_require(raw, "name", "agent_card"),
        description=_require(raw, "description", "agent_card"),
        version=_require(raw, "version", "agent_card"),
        public_url=_require(raw, "public_url", "agent_card"),
        protocol_binding=raw.get("protocol_binding", "JSONRPC"),
        protocol_version=raw.get("protocol_version", "1.0"),
        default_input_modes=list(raw.get("default_input_modes", [])),
        default_output_modes=list(raw.get("default_output_modes", [])),
        streaming=bool(raw.get("streaming", False)),
        skills=[_parse_skill(s, "agent_card.skills") for s in raw.get("skills", [])],
        extended=extended,
    )


def _parse_llm(raw: dict[str, Any]) -> LLMConfig:
    api_key_env = _require(raw, "api_key_env", "llm")
    api_key = os.getenv(api_key_env, "").strip()
    return LLMConfig(
        provider=_require(raw, "provider", "llm"),
        base_url=_require(raw, "base_url", "llm"),
        model=_require(raw, "model", "llm"),
        api_key_env=api_key_env,
        api_key=api_key,
        temperature=float(raw.get("temperature", 0.7)),
        timeout_seconds=float(raw.get("timeout_seconds", 120)),
        system_prompt=raw.get("system_prompt", ""),
    )


def load_config(config_path: Path | None = None) -> AppConfig:
    """读取配置文件与环境变量，返回装配好的 AppConfig。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}")

    load_dotenv(DEFAULT_ENV_PATH, override=False)

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    server_raw = _require(raw, "server", "根配置")
    server = ServerConfig(
        host=server_raw.get("host", "127.0.0.1"),
        port=int(server_raw.get("port", 9999)),
        log_level=server_raw.get("log_level", "INFO"),
    )

    agent_card = _parse_agent_card(_require(raw, "agent_card", "根配置"))

    executor_mode = _require(raw, "executor", "根配置").get("mode", EXECUTOR_MODE_ECHO)
    if executor_mode not in VALID_EXECUTOR_MODES:
        raise ConfigError(
            f"executor.mode 只能是 {VALID_EXECUTOR_MODES}，当前是 {executor_mode!r}"
        )

    llm = _parse_llm(_require(raw, "llm", "根配置"))

    client_raw = raw.get("client") or {}
    client = ClientSettings(
        timeout_seconds=float(client_raw.get("timeout_seconds", 120)),
    )

    if executor_mode == EXECUTOR_MODE_LLM and not llm.api_key:
        raise ConfigError(
            f"executor.mode=llm 需要环境变量 {llm.api_key_env}。"
            f"请复制 .env.example 为 .env 并填入真实 API Key（{DEFAULT_ENV_PATH}）。"
        )

    return AppConfig(
        server=server,
        agent_card=agent_card,
        executor_mode=executor_mode,
        llm=llm,
        client=client,
        config_path=path,
    )