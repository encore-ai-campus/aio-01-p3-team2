"""Tool Guard와 제한 시간을 적용해 MCP Tool을 호출한다."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

from ..guards.tool_guard import ToolGuard

ToolTransport = Callable[[str, dict[str, str]], Awaitable[Mapping[str, object]]]


class MCPTimeoutError(TimeoutError):
    """MCP 호출 제한 시간 초과 또는 연결 실패를 나타낸다."""

    def __init__(self, tool_name: str) -> None:
        """실패한 Tool 이름만 보관하고 통신 오류의 원문은 노출하지 않는다."""
        self.tool_name = tool_name
        super().__init__(f"MCP 통신에 실패했습니다: {tool_name}")


class MCPToolError(RuntimeError):
    """MCP Tool 실행 실패 또는 결과 형식 오류를 나타낸다."""

    def __init__(self, tool_name: str) -> None:
        """실패한 Tool 이름만 보관하고 내부 예외 원문은 노출하지 않는다."""
        self.tool_name = tool_name
        super().__init__(f"MCP Tool 실행에 실패했습니다: {tool_name}")


class MCPClient:
    """주입된 전송 함수를 이용해 허용된 MCP Tool만 호출한다.

    전송 함수는 MCP SDK 응답을 문자열 키의 Mapping으로 정규화해야 한다.
    SDK의 연결 오류는 ``ConnectionError``, 시간 초과는 ``TimeoutError``로
    변환해야 한다. 실제 MCP 연결과 Mock 전송을 같은 규격으로 교체할 수 있다.
    """

    def __init__(
        self,
        transport: ToolTransport,
        tool_guard: ToolGuard,
        timeout_seconds: float,
    ) -> None:
        """전송 함수, Tool Guard, 호출당 제한 시간을 저장한다."""
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("MCP 제한 시간은 숫자여야 합니다.")
        if timeout_seconds <= 0:
            raise ValueError("MCP 제한 시간은 0보다 커야 합니다.")
        self._transport = transport
        self._tool_guard = tool_guard
        self._timeout_seconds = timeout_seconds

    async def call_tool(
        self,
        agent_name: str,
        tool_name: str,
        arguments: Mapping[str, str],
        *,
        primary_failed: bool = False,
    ) -> dict[str, object]:
        """Tool Guard 검사 후 MCP를 호출하고 통신·Tool 오류를 구분한다.

        정책 위반은 ``ToolGuardError``로 그대로 전달한다. Timeout과 연결
        실패는 ``mcp_timeout``, 나머지 Tool 오류는 ``mcp_tool_error``로
        Workflow Engine에서 매핑할 수 있도록 별도 예외로 반환한다.
        """
        self._tool_guard.validate_call(
            agent_name, tool_name, arguments, primary_failed=primary_failed
        )
        try:
            result = await asyncio.wait_for(
                self._transport(tool_name, dict(arguments)), timeout=self._timeout_seconds
            )
        except (TimeoutError, ConnectionError) as exc:
            raise MCPTimeoutError(tool_name) from exc
        except Exception as exc:
            raise MCPToolError(tool_name) from exc

        if not isinstance(result, Mapping):
            raise MCPToolError(tool_name)
        return dict(result)

    async def get_game_rules(self, agent_name: str, content_id: str) -> dict[str, object]:
        """콘텐츠 ID로 Primary 게임 규칙을 조회한다."""
        return await self.call_tool(agent_name, "get_game_rules", {"content_id": content_id})

    async def get_game_rules_fallback(
        self, agent_name: str, content_id: str, *, primary_failed: bool
    ) -> dict[str, object]:
        """Primary Tool 실패 후에만 검증된 규칙 Snapshot을 조회한다."""
        return await self.call_tool(
            agent_name,
            "get_game_rules_fallback",
            {"content_id": content_id},
            primary_failed=primary_failed,
        )

    async def get_item_info(self, agent_name: str, item_id: str) -> dict[str, object]:
        """아이템 ID로 보상 아이템 정보를 조회한다."""
        return await self.call_tool(agent_name, "get_item_info", {"item_id": item_id})


def create_streamable_http_transport(url: str) -> ToolTransport:
    """MCP Python SDK 1.x의 Streamable HTTP Tool 전송 함수를 만든다.

    MCP 담당 서버의 ``/mcp`` URL을 받는다. SDK 연결·세션 오류는
    ``ConnectionError``로, Tool의 ``isError``와 결과 형식 오류는
    일반 실행 오류로 변환해 MCPClient가 복구 경로를 구분하게 한다.
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("MCP 서버의 HTTP URL이 필요합니다.")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def transport(tool_name: str, arguments: dict[str, str]) -> Mapping[str, object]:
        """SDK 세션을 열고 구조화된 Tool 결과만 반환한다."""
        try:
            async with (
                streamablehttp_client(url) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            raise ConnectionError("MCP 세션 통신에 실패했습니다.") from exc

        if result.isError:
            raise RuntimeError("MCP Tool이 실행 오류를 반환했습니다.")
        structured = result.structuredContent
        if structured is None and len(result.content) == 1:
            block = result.content[0]
            if block.type == "text":
                structured = json.loads(block.text)
        if not isinstance(structured, Mapping):
            raise TypeError("MCP Tool 결과는 객체여야 합니다.")
        return dict(structured)

    return transport
