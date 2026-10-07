"""统一的本地/容器服务启动器（工单 09 本地起服务用）。

A2A_LOCAL=1 时绑 127.0.0.1（避免 Windows 对 0.0.0.0 的防火墙弹窗）；否则用配置里的 host。
serve(..., bind_override=...) 可显式指定绑定地址（如 UI 开局域网时强制 0.0.0.0），
优先级高于 A2A_LOCAL；不传则维持原逻辑。
uvicorn.run(log_config=None) 保留统一日志格式（票 05 教训 5）。
"""

from __future__ import annotations

import os

from common.logging_setup import setup_logging


def _host(cfg_host: str) -> str:
    if os.environ.get("A2A_LOCAL", "").strip().lower() in ("1", "true", "yes"):
        return "127.0.0.1"
    return cfg_host


def serve(app, host: str, port: int, log_level: str = "INFO", bind_override: str | None = None) -> None:
    import uvicorn

    setup_logging(log_level)
    h = (bind_override or "").strip() or _host(host)
    uvicorn.run(app, host=h, port=int(port), log_config=None)
