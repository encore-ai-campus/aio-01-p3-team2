"""Opong Heroes game data MCP server.

Run from the repository root with: python -m mcp_server.server
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp_server.tools.get_clash_event_sample import get_clash_event_sample
from mcp_server.tools.get_event_planning_context import get_event_planning_context
from mcp_server.tools.get_game_rules import get_game_rules
from mcp_server.tools.get_game_rules_fallback import get_game_rules_fallback
from mcp_server.tools.get_item_info import get_item_info

if __name__ == "__main__":
    load_dotenv(Path(__file__).with_name(".env"), override=False)

mcp = FastMCP(
    "game_mcp_server",
    instructions="Opong Heroes 콘텐츠 규칙, 유저 성장 기준, 아이템을 조회합니다.",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8010")),
    stateless_http=True,
    json_response=True,
)

for tool in (
    get_event_planning_context,
    get_game_rules,
    get_game_rules_fallback,
    get_item_info,
    get_clash_event_sample,
):
    mcp.tool()(tool)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
