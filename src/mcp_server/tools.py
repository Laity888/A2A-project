"""MCP 工具实现（工单 11）。纯函数、可注入依赖、不含 LLM。

kb_search 桥接检索 A2A Agent（retrieval 可注入 fake 做离线测）；
create_ticket / get_order_status / escalate_human 返回确定性 mock 数据，演示多工具路由。
"""

from __future__ import annotations

import hashlib
from typing import Awaitable, Callable, Optional

RetrievalFn = Callable[..., Awaitable[dict]]


async def kb_search(query: str, context_id: str = "", retrieval: Optional[RetrievalFn] = None) -> dict:
    """在 RAGFlow 知识库检索与 query 相关的资料片段。context_id 由接待注入（透传给检索 A2A）。"""
    if retrieval is None:
        from mcp_server.retrieval_bridge import default_retrieval
        retrieval = default_retrieval()
    return await retrieval(query, context_id)


def create_ticket(issue: str, contact: str = "") -> dict:
    """创建客服工单（mock）。"""
    tid = "TK-" + hashlib.md5(issue.encode("utf-8")).hexdigest()[:8].upper()
    return {
        "status": "created",
        "ticket_id": tid,
        "summary": (issue or "")[:80],
        "contact": contact,
        "note": "已受理，客服将在 24 小时内跟进（mock）。",
    }


def get_order_status(order_id: str) -> dict:
    """查询订单状态（mock）。"""
    return {
        "order_id": order_id,
        "status": "已发货",
        "carrier": "顺丰",
        "eta": "预计 3 天内送达",
        "note": "mock 数据，未接真实订单系统。",
    }


def escalate_human(reason: str = "") -> dict:
    """转接人工客服（mock）。"""
    return {
        "escalated": True,
        "queue": "human",
        "reason": reason,
        "note": "已转人工，请稍候（mock）。",
    }
