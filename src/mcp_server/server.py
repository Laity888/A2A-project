"""MCP 服务器：注册工具、stdio 运行（工单 11）。

注意 Windows stdio：日志走 stderr，绝不 print 到 stdout（会破坏 JSON-RPC 帧）。
"""

from __future__ import annotations

import logging

from mcp.server import MCPServer

from mcp_server import tools
from mcp_server.config import load_mcp_config
from mcp_server.retrieval_bridge import make_a2a_retrieval

logger = logging.getLogger(__name__)

mcp = MCPServer("a2a-cs-tools")

_cfg = load_mcp_config()
_retrieval = make_a2a_retrieval(
    _cfg["retrieval_url"], float(_cfg.get("client_timeout_seconds", 120)), int(_cfg.get("top_k", 8))
)


@mcp.tool()
async def kb_search(query: str) -> dict:
    """在 RAGFlow 知识库检索与问题相关的资料片段（返回 chunks + 出处）。"""
    return await tools.kb_search(query, retrieval=_retrieval)


@mcp.tool()
def create_ticket(issue: str, contact: str = "") -> dict:
    """为无法即时解决的客服问题创建工单。"""
    return tools.create_ticket(issue, contact)


@mcp.tool()
def get_order_status(order_id: str) -> dict:
    """按订单号查询订单状态。"""
    return tools.get_order_status(order_id)


@mcp.tool()
def escalate_human(reason: str = "") -> dict:
    """将对话转接给人工客服。"""
    return tools.escalate_human(reason)
