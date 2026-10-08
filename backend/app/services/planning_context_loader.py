"""Backend A boundary for the MCP planning-context lookup."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from backend.app.services.mcp_client import MCPTimeoutError, MCPToolError

PlanningContextTransport = Callable[[str, dict[str, object]], Awaitable[Mapping[str, object]]]


class PlanningContextLoader:
    """Load the verified envelope before Backend B asks Planner for a plan.

    Backend B owns retries, fallback selection, detailed game-data validation,
    and Trace emission. This boundary only calls the fixed MCP tool and rejects
    an unusable response before passing it to the workflow.
    """

    TOOL_NAME = "get_event_planning_context"

    def __init__(self, transport: PlanningContextTransport, timeout_seconds: float = 5) -> None:
        if timeout_seconds <= 0:
            raise ValueError("기획용 MCP 조회 제한 시간은 0보다 커야 합니다.")
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def load(self, target_user: str, duration: int) -> dict[str, object]:
        if target_user not in {"new_user", "returning_user", "all_user"}:
            raise ValueError("이벤트 대상이 올바르지 않습니다.")
        if type(duration) is not int or not 1 <= duration <= 7:
            raise ValueError("이벤트 기간은 1~7일이어야 합니다.")

        try:
            result = await asyncio.wait_for(
                self._transport(self.TOOL_NAME, {"target_user": target_user, "duration": duration}),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, ConnectionError) as exc:
            raise MCPTimeoutError(self.TOOL_NAME) from exc
        except Exception as exc:
            raise MCPToolError(self.TOOL_NAME) from exc

        if not isinstance(result, Mapping):
            raise MCPToolError(self.TOOL_NAME)
        context = dict(result)
        if not all(key in context for key in ("contents", "items", "user_segments")):
            raise MCPToolError(self.TOOL_NAME)
        if (
            not isinstance(context.get("game_data_version"), str)
            or not context["game_data_version"].strip()
        ):
            raise MCPToolError(self.TOOL_NAME)
        if context.get("source") not in {"primary", "fallback_snapshot"}:
            raise MCPToolError(self.TOOL_NAME)
        return context
