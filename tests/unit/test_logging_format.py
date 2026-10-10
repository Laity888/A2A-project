"""日志格式冒烟（工单 01）。验证「模块:类.函数:行号」统一格式（票 05 硬约束）。"""

from __future__ import annotations

import io
import logging
import re

from common.logging_setup import DATE_FORMAT, LOG_FORMAT, ClassNameFilter


def test_log_line_includes_location():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    handler.addFilter(ClassNameFilter())

    logger = logging.getLogger("test.logging.fmt")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel("INFO")

    class Widget:
        def ping(self):
            logger.info("marker-1234")

    Widget().ping()

    out = stream.getvalue()
    assert "marker-1234" in out
    # 期望形如  module:Widget.ping:NN
    assert re.search(r"[\w.]+:Widget\.ping:\d+", out), out
