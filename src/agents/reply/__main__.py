"""python -m agents.reply —— 本地起答复 Agent（默认 8003）。"""

from __future__ import annotations

from agents.reply.app import build_app
from common.config import load_agent
from common.serve import serve


def main() -> None:
    cfg = load_agent("reply")
    serve(build_app(cfg), cfg.server.host, cfg.server.port, cfg.common.logging.level)


if __name__ == "__main__":
    main()
