"""python -m mcp_server —— 以 stdio 起 MCP 工具服务器（工单 11）。"""

from __future__ import annotations


def main() -> None:
    from mcp_server.server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
