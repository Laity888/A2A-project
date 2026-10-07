"""统一日志（新代码的规范入口）。

复用 hello_world.logging_setup 已实测通过的「模块:类.函数:行号」实现，
避免重复那份需要遍历调用栈补类名、跳过框架帧的易错逻辑。
TODO(架构收尾)：后续可把实现迁到本模块、让 hello_world 反向引用，去掉对锚点的耦合。
"""

from __future__ import annotations

from hello_world.logging_setup import (
    DATE_FORMAT,
    LOG_FORMAT,
    ClassNameFilter,
    setup_logging,
)

__all__ = ["setup_logging", "ClassNameFilter", "LOG_FORMAT", "DATE_FORMAT"]
