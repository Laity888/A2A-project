"""配置加载：config/common.yaml + config/agents/<name>.yaml → 强类型对象。

规则（沿用 hello_world/config.py 的约定）：
- 任何可变参数都来自配置文件，代码里不硬编码。
- 密钥类信息只从环境变量读取（.env 由 python-dotenv 加载）。
- 找不到必需项时直接报错并说清怎么补，不做静默兜底。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_COMMON_PATH = CONFIG_DIR / "common.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

# 三个业务 Agent 的名字（对应 config/agents/<name>.yaml）
AGENT_RECEPTION = "reception"
AGENT_RETRIEVAL = "retrieval"
AGENT_REPLY = "reply"
VALID_AGENTS = (AGENT_RECEPTION, AGENT_RETRIEVAL, AGENT_REPLY)


class ConfigError(RuntimeError):
    """配置缺失或非法时抛出，消息里带上补救办法。"""


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class MilvusConfig:
    host: str
    port: int
    database: str
    collection: str


@dataclass(frozen=True)
class EndpointConfig:
    host: str
    port: int


@dataclass(frozen=True)
class InfraConfig:
    milvus: MilvusConfig
    mysql: EndpointConfig
    redis: EndpointConfig


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    api_key_env: str
    api_key: str
    temperature: float
    timeout_seconds: float
    max_retries: int
    max_tokens: int | None = None


@dataclass(frozen=True)
class CommonConfig:
    logging: LoggingConfig
    infra: InfraConfig
    llm: LLMConfig


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


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    public_url: str


@dataclass(frozen=True)
class AgentConfig:
    name: str
    server: ServerConfig
    card: AgentCardConfig
    prompts: dict[str, Any]
    # 该 Agent 的其余配置段（orchestration / session / performance / retrieval ...）
    # 保留为原始 dict，由各 Agent 实现按票 04/06/11 读取，避免过早建一堆薄类型。
    settings: dict[str, Any]
    common: CommonConfig
    config_path: Path


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"配置缺少 {where}.{key}")
    return mapping[key]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _parse_llm(raw: dict[str, Any]) -> LLMConfig:
    api_key_env = _require(raw, "api_key_env", "llm")
    api_key = os.getenv(api_key_env, "").strip()
    mt = raw.get("max_tokens")
    return LLMConfig(
        provider=_require(raw, "provider", "llm"),
        base_url=_require(raw, "base_url", "llm"),
        model=_require(raw, "model", "llm"),
        api_key_env=api_key_env,
        api_key=api_key,
        temperature=float(raw.get("temperature", 0.3)),
        timeout_seconds=float(raw.get("timeout_seconds", 120)),
        max_retries=int(raw.get("max_retries", 1)),
        max_tokens=(int(mt) if mt is not None else None),
    )


def _parse_infra(raw: dict[str, Any]) -> InfraConfig:
    milvus_raw = _require(raw, "milvus", "infra")
    mysql_raw = _require(raw, "mysql", "infra")
    redis_raw = _require(raw, "redis", "infra")
    return InfraConfig(
        milvus=MilvusConfig(
            host=_require(milvus_raw, "host", "infra.milvus"),
            port=int(_require(milvus_raw, "port", "infra.milvus")),
            database=_require(milvus_raw, "database", "infra.milvus"),
            collection=_require(milvus_raw, "collection", "infra.milvus"),
        ),
        mysql=EndpointConfig(
            host=_require(mysql_raw, "host", "infra.mysql"),
            port=int(_require(mysql_raw, "port", "infra.mysql")),
        ),
        redis=EndpointConfig(
            host=_require(redis_raw, "host", "infra.redis"),
            port=int(_require(redis_raw, "port", "infra.redis")),
        ),
    )


_LOCAL_SERVICE_NAMES = ("reception", "retrieval", "reply", "ui")


def apply_local_override(raw: Any) -> Any:
    """A2A_LOCAL=1 时，把容器 service-name URL 改写成 127.0.0.1（本地起服务用）。"""
    if os.environ.get("A2A_LOCAL", "").strip().lower() not in ("1", "true", "yes"):
        return raw

    def walk(x: Any) -> Any:
        if isinstance(x, str):
            for n in _LOCAL_SERVICE_NAMES:
                x = x.replace(f"http://{n}:", "http://127.0.0.1:")
            return x
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(raw)


def load_common(config_path: Path | None = None) -> CommonConfig:
    """读取 config/common.yaml，装配成 CommonConfig。"""
    path = Path(config_path) if config_path else DEFAULT_COMMON_PATH
    load_dotenv(DEFAULT_ENV_PATH, override=False)
    raw = apply_local_override(_load_yaml(path))

    logging_raw = _require(raw, "logging", "根配置")
    infra = _parse_infra(_require(raw, "infra", "根配置"))
    llm = _parse_llm(_require(raw, "llm", "根配置"))

    return CommonConfig(
        logging=LoggingConfig(level=logging_raw.get("level", "INFO")),
        infra=infra,
        llm=llm,
    )


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


def _parse_card(raw: dict[str, Any]) -> AgentCardConfig:
    return AgentCardConfig(
        name=_require(raw, "name", "agent_card"),
        description=_require(raw, "description", "agent_card"),
        version=_require(raw, "version", "agent_card"),
        public_url=_require(raw, "public_url", "agent_card"),
        protocol_binding=raw.get("protocol_binding", "JSONRPC"),
        protocol_version=str(raw.get("protocol_version", "1.0")),
        default_input_modes=list(raw.get("default_input_modes", [])),
        default_output_modes=list(raw.get("default_output_modes", [])),
        streaming=bool(raw.get("streaming", False)),
        skills=[_parse_skill(s, "agent_card.skills") for s in raw.get("skills", [])],
    )


def _agent_config_path(name: str) -> Path:
    if name not in VALID_AGENTS:
        raise ConfigError(f"未知 Agent：{name!r}，可选 {VALID_AGENTS}")
    return CONFIG_DIR / "agents" / f"{name}.yaml"


def load_agent(name: str, config_path: Path | None = None) -> AgentConfig:
    """加载某个 Agent 的配置：合并 common.yaml + agents/<name>.yaml。"""
    common = load_common()
    path = Path(config_path) if config_path else _agent_config_path(name)
    raw = apply_local_override(_load_yaml(path))

    server_raw = _require(raw, "server", "根配置")
    public_url = _require(server_raw, "public_url", "server")
    server = ServerConfig(
        host=server_raw.get("host", "0.0.0.0"),
        port=int(_require(server_raw, "port", "server")),
        public_url=public_url,
    )

    card_raw = _require(raw, "agent_card", "根配置")
    # Agent Card 的对外地址与 server.public_url 保持一致（票 05 教训）
    card_raw = {**card_raw, "public_url": card_raw.get("public_url", public_url)}
    card = _parse_card(card_raw)

    typed_keys = {"server", "agent_card", "prompts"}
    settings = {k: v for k, v in raw.items() if k not in typed_keys}

    return AgentConfig(
        name=name,
        server=server,
        card=card,
        prompts=dict(raw.get("prompts") or {}),
        settings=settings,
        common=common,
        config_path=path,
    )


def llm_for(cfg: "AgentConfig", **overrides: Any) -> LLMConfig:
    """基于 common.llm 派生一份带覆盖项的 LLMConfig（每角色模型/超时/限长）。

    只应用非 None 的覆盖：model / temperature / timeout_seconds / max_retries / max_tokens。
    无覆盖时原样返回 common.llm。用于分流/ReAct 用快模型、答复用强模型等，全走配置不硬编码。
    """
    allowed = ("model", "temperature", "timeout_seconds", "max_retries", "max_tokens")
    over = {k: v for k, v in overrides.items() if k in allowed and v is not None}
    if not over:
        return cfg.common.llm
    if "temperature" in over:
        over["temperature"] = float(over["temperature"])
    if "timeout_seconds" in over:
        over["timeout_seconds"] = float(over["timeout_seconds"])
    if "max_retries" in over:
        over["max_retries"] = int(over["max_retries"])
    if "max_tokens" in over:
        over["max_tokens"] = int(over["max_tokens"])
    return replace(cfg.common.llm, **over)
