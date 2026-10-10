"""UI 配置加载（config/ui.yaml）。"""

from __future__ import annotations

from typing import Any

import yaml

from common.config import CONFIG_DIR, apply_local_override


def load_ui_config() -> dict[str, Any]:
    path = CONFIG_DIR / "ui.yaml"
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return apply_local_override(raw.get("ui", {}))
