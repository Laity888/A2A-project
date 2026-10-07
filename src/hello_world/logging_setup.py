"""日志配置：统一输出格式，标记「模块 : 类 . 函数 : 行号」。

行号由 logging 的 %(lineno)d 提供；类名 logging 本身不提供，
通过向上遍历调用栈取当前方法所属的实例类型来补全。
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-7s | "
    "%(module)s:%(classname)s.%(funcName)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 这些模块只负责转发日志，不是真正的调用方，遍历时跳过
_SKIP_MODULE_PREFIXES = (
    "logging",
    "uvicorn",
    "anyio",
    "asyncio",
    "starlette",
    __name__,  # 本模块：filter 自己也算一帧
)


def _resolve_class_name() -> str:
    """从当前调用栈里找出第一个业务帧，返回它所属的类名。"""
    frame = sys._getframe(1)
    while frame is not None:
        module_name = frame.f_globals.get("__name__", "")
        if not module_name.startswith(_SKIP_MODULE_PREFIXES):
            owner = frame.f_locals.get("self", frame.f_locals.get("cls"))
            if owner is not None:
                return owner.__name__ if isinstance(owner, type) else type(owner).__name__
            return "-"
        frame = frame.f_back
    return "-"


class ClassNameFilter(logging.Filter):
    """给每条日志记录补上 classname 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.classname = _resolve_class_name()
        return True


def setup_logging(level: str = "INFO") -> None:
    """把根 logger 配置成统一格式，业务与框架日志走同一套输出。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    handler.addFilter(ClassNameFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())