"""실제 API 호출 없이 LLM·MCP SDK 전송 어댑터의 경계를 시험한다."""

import sys
import types
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from backend.app.guards.tool_guard import ToolGuard
from backend.app.services.llm_service import create_openai_transport
from backend.app.services.mcp_client import (
    MCPClient,
    MCPToolError,
    create_streamable_http_transport,
)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    """SDK 응답의 구조화 데이터와 Tool 오류를 분리해 확인한다."""

    async def test_openai_transport_requests_strict_json_schema(self) -> None:
        """Planner 요청에 Agent 지시와 엄격한 JSON Schema를 함께 전달한다."""
        calls: list[dict[str, object]] = []

        class FakeResponses:
            """Responses API 호출 인자를 기록하는 시험 객체다."""

            async def create(self, **kwargs):
                """구조화된 Planner 응답을 반환한다."""
                calls.append(kwargs)
                return types.SimpleNamespace(output_text='{"target_user":"new_user"}')

        class FakeOpenAI:
            """네트워크 없이 Responses API 모양을 제공한다."""

            def __init__(self) -> None:
                """가짜 Responses 리소스를 만든다."""
                self.responses = FakeResponses()

            async def close(self) -> None:
                """실제 연결이 없으므로 종료할 자원이 없다."""

        fake_module = types.ModuleType("openai")
        fake_module.AsyncOpenAI = FakeOpenAI
        fake_module.APITimeoutError = TimeoutError
        with patch.dict(sys.modules, {"openai": fake_module}):
            transport = create_openai_transport(
                "test-model",
                {"workers": {"event_planner_agent": {"instructions": "기획안을 작성한다."}}},
            )
            result = await transport("event_planner_agent", {"user_request": {}})

        self.assertEqual(result["target_user"], "new_user")
        self.assertEqual(calls[0]["instructions"], "기획안을 작성한다.")
        self.assertTrue(calls[0]["text"]["format"]["strict"])

    async def test_mcp_transport_unwraps_structured_result(self) -> None:
        """SDK의 structuredContent를 Tool 결과 객체로 전달한다."""

        class FakeSession:
            """MCP Session의 필요한 메서드만 제공한다."""

            def __init__(self, read, write) -> None:
                """시험용 스트림 인자를 받는다."""

            async def __aenter__(self):
                """가짜 세션을 연다."""
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                """가짜 세션을 닫는다."""

            async def initialize(self) -> None:
                """초기화 호출을 허용한다."""

            async def call_tool(self, name, arguments):
                """정상 또는 오류 Tool 결과를 반환한다."""
                return types.SimpleNamespace(
                    isError=name == "get_game_rules_fallback",
                    structuredContent={"content_id": arguments["content_id"]},
                    content=[],
                )

        @asynccontextmanager
        async def fake_http(url):
            """SDK Streamable HTTP Context Manager의 형태를 흉내 낸다."""
            yield None, None, None

        mcp_module = types.ModuleType("mcp")
        client_module = types.ModuleType("mcp.client")
        http_module = types.ModuleType("mcp.client.streamable_http")
        mcp_module.ClientSession = FakeSession
        http_module.streamablehttp_client = fake_http
        with patch.dict(
            sys.modules,
            {
                "mcp": mcp_module,
                "mcp.client": client_module,
                "mcp.client.streamable_http": http_module,
            },
        ):
            transport = create_streamable_http_transport("http://127.0.0.1:8010/mcp")

        guard = ToolGuard(
            {
                "workers": {
                    "balance_rule_agent": {
                        "allowed_tools": ["get_game_rules", "get_game_rules_fallback"]
                    }
                }
            }
        )
        client = MCPClient(transport, guard, 1)
        result = await client.get_game_rules("balance_rule_agent", "HUNT_001")
        self.assertEqual(result["content_id"], "HUNT_001")
        with self.assertRaises(MCPToolError):
            await client.get_game_rules_fallback(
                "balance_rule_agent", "HUNT_001", primary_failed=True
            )
