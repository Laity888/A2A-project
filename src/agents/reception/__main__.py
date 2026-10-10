"""python -m agents.reception —— 本地起接待 Agent（默认 8001）。"""

from __future__ import annotations

from agents.reception.app import build_app
from common.config import load_agent
from common.serve import serve


def main() -> None:
    cfg = load_agent("reception")
    serve(build_app(cfg), cfg.server.host, cfg.server.port, cfg.common.logging.level)


if __name__ == "__main__":
    main()
