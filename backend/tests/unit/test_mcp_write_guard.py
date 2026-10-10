"""MCP tools cannot perform game writes, even when an Agent requests one."""

import unittest

from backend.app.guards.tool_guard import ToolGuard, ToolGuardError
from backend.app.services.mcp_client import MCPClient


class MCPWriteGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_write_tool_is_rejected_before_transport(self) -> None:
        transport_calls: list[tuple[str, dict[str, object]]] = []

        async def transport(tool_name: str, arguments: dict[str, object]):
            transport_calls.append((tool_name, arguments))
            return {"ok": True}

        client = MCPClient(
            transport,
            ToolGuard(
                {"workers": {"event_planner_agent": {"allowed_tools": ["register_game_event"]}}}
            ),
            timeout_seconds=1,
        )

        with self.assertRaises(ToolGuardError):
            await client.call_tool("event_planner_agent", "register_game_event", {"event": "x"})

        self.assertEqual(transport_calls, [])
