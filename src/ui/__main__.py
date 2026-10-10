"""python -m ui —— 本地起 Web UI（BFF，默认 8080）。

单一开关：config/ui.yaml 的 `lan_access`
- true  → 绑 0.0.0.0，局域网可访问（仍需系统防火墙放行该端口；agent 仍留 127.0.0.1）
- false → 维持默认（A2A_LOCAL=1 时绑 127.0.0.1，仅本机）
"""

from __future__ import annotations

import logging

from common.config import load_common
from common.serve import serve
from ui.app import build_app
from ui.config import load_ui_config

logger = logging.getLogger(__name__)


def main() -> None:
    ui_cfg = load_ui_config()
    port = int(ui_cfg.get("port", 8080))
    lan = bool(ui_cfg.get("lan_access", False))
    bind_override = "0.0.0.0" if lan else None   # None → 交给 serve 的 A2A_LOCAL/config 逻辑
    if lan:
        logger.info("UI 局域网模式：绑定 0.0.0.0:%d（同网段访问 http://<本机局域网IP>:%d）", port, port)
    serve(build_app(ui_cfg), ui_cfg.get("host", "0.0.0.0"), port,
          log_level=load_common().logging.level, bind_override=bind_override)


if __name__ == "__main__":
    main()
