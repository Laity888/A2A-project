"""把接待的 A2A 流事件翻译成前端规范化事件（票 07）。

规范化事件：{kind: status|sources|token|trace|done|error, ...}。
真实流来自 create_client(streaming=True)；翻译尽量健壮，缺字段就跳过。
"""

from __future__ import annotations

from typing import Any

from a2a.helpers import get_artifact_text, get_data_parts, get_message_text


def _artifacts(chunk) -> list:
    if chunk.HasField("task"):
        return list(chunk.task.artifacts)
    if chunk.HasField("artifact_update"):
        return [chunk.artifact_update.artifact]
    return []


def _status_message(status) -> str:
    """从 TaskStatus 里取人类可读的阶段文案（不带入正文）。"""
    msg = getattr(status, "message", None)
    if not msg:
        return ""
    try:
        return get_message_text(msg) or ""
    except Exception:  # noqa: BLE001 - 文案提取尽力而为
        return ""


def a2a_to_events(chunk) -> list[dict]:
    events: list[dict] = []

    # 状态：只发状态事件 + 阶段文案，不把状态文本当正文（避免 "processing" 漏进气泡）
    if chunk.HasField("status_update"):
        events.append({
            "kind": "status",
            "state": str(chunk.status_update.status.state),
            "message": _status_message(chunk.status_update.status),
        })
    elif chunk.HasField("task"):
        events.append({
            "kind": "status",
            "state": str(chunk.task.status.state),
            "message": _status_message(chunk.task.status),
        })

    for art in _artifacts(chunk):
        text = get_artifact_text(art)
        if text:
            events.append({"kind": "token", "text": text})
        for data in get_data_parts(list(art.parts)):
            if not isinstance(data, dict):
                continue
            if "sources" in data:
                events.append({"kind": "sources", "data": data})
            elif {"intent", "retrieval", "timings"} & data.keys():
                events.append({"kind": "trace", "trace": data})

    return events
