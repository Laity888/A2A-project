"""服务端入口：加载配置 -> 配置日志 -> 启动 ASGI 服务。"""

from __future__ import annotations

import logging
import sys

import uvicorn

from hello_world.app import build_app
from hello_world.config import ConfigError, load_config
from hello_world.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    setup_logging(config.server.log_level)

    logger.info("加载配置：%s", config.config_path)
    logger.info(
        "启动服务 host=%s port=%s mode=%s",
        config.server.host,
        config.server.port,
        config.executor_mode,
    )

    # log_config=None：不让 uvicorn 覆盖上面的统一日志格式
    uvicorn.run(
        build_app(config),
        host=config.server.host,
        port=config.server.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()