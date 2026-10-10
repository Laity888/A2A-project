"""SSE 事件格式化（纯函数，离线可测）。"""

from __future__ import annotations

import json
from typing import Any


def format_sse(event: str, data: dict[str, Any]) -> str:
    """一条 SSE：event: <name>\\ndata: <json>\\n\\n。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
