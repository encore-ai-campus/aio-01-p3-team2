"""Opong Heroes game data MCP server.

Run from the repository root with: python -m mcp_server.server
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp_server.tools.get_game_rules import get_game_rules
from mcp_server.tools.get_game_rules_fallback import get_game_rules_fallback
from mcp_server.tools.get_item_info import get_item_info

mcp = FastMCP(
    "game_mcp_server",
    instructions="Opong Heroes 콘텐츠 규칙, 유저 성장 기준, 아이템을 조회합니다.",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8010")),
    stateless_http=True,
    json_response=True,
)

for tool in (get_game_rules, get_game_rules_fallback, get_item_info):
    mcp.tool()(tool)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
