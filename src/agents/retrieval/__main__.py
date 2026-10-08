"""python -m agents.retrieval —— 本地起检索 Agent（默认 8002，启动会加载 BGE-M3）。"""

from __future__ import annotations

from agents.retrieval.app import build_app
from common.config import load_agent
from common.serve import serve


def main() -> None:
    cfg = load_agent("retrieval")
    serve(build_app(cfg), cfg.server.host, cfg.server.port, cfg.common.logging.level)


if __name__ == "__main__":
    main()
