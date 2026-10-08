"""MCP 服务器配置加载（config/mcp.yaml），复用 A2A_LOCAL 的 URL 改写。"""

from __future__ import annotations

from typing import Any

import yaml

from common.config import CONFIG_DIR, apply_local_override


def load_mcp_config() -> dict[str, Any]:
    path = CONFIG_DIR / "mcp.yaml"
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return apply_local_override(raw.get("mcp", {}))
