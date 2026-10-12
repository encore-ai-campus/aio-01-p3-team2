"""Shared read-only MCP recovery loop; Supervisor owns every recovery decision."""

from typing import Any

from backend.app.agents.supervisor import Supervisor, SupervisorWorkflowState
from backend.app.faults.error_catalog import error_definition
from backend.app.orchestration.retry import wait_before_retry
from backend.app.services.recovery_policy import pinned_policy_version
from backend.app.services.workflow_execution import append_recovery_event, publish_decision


class MCPRecoveryUnavailable(RuntimeError):
    """A read-only MCP operation reached its policy-defined safe stop."""

    def __init__(
        self,
        tool_name: str,
        error_type: str,
        recovery_events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.error_type = error_type
        self.error_code = error_definition(error_type).code
        self.recovery_events = recovery_events or []
        super().__init__(error_definition(error_type).safe_message)


async def _run_mcp_read_async(
    agent_name: str,
    tool_name: str,
    operation: Any,
    *,
    fallback_tool: str | None = None,
    fallback_operation: Any = None,
    supervisor: Supervisor | None = None,
    state_cursor: list[SupervisorWorkflowState] | None = None,
    recovery_decider: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run an idempotent read under the pinned recovery policy and keep its decision trace."""
    from backend.app.services.mcp_client import MCPTimeoutError, MCPToolError

    decision_maker = supervisor or Supervisor({"context_access": {}})
    retry_count = 0
    recovery_events: list[dict[str, Any]] = []
    while True:
        try:
            result = await operation()
            if retry_count:
                append_recovery_event(
                    recovery_events,
                    {
                        "event": "mcp.recovery_resolved",
                        "status": "SUCCESS",
                        "agent": agent_name,
                        "tool": tool_name,
                        "retry_count": retry_count,
                        "policy_version": pinned_policy_version(),
                    },
                )
            return result, recovery_events
        except MCPTimeoutError as exc:
            error_type = "mcp_timeout"
            failure = exc
        except MCPToolError as exc:
            error_type = getattr(exc, "error_type", "mcp_tool_error")
            failure = exc
        definition = error_definition(error_type)
        decide = recovery_decider or decision_maker.review_failure
        route = await decide(
            error_type,
            current_agent=agent_name,
            failed_tool=tool_name,
            retry_count=retry_count,
        )
        if state_cursor is not None:
            decision = decision_maker.record_recovery_decision(
                state_cursor[0],
                route,
                error_code=definition.code,
                evidence_refs=(
                    f"workflow:{state_cursor[0].workflow_id}",
                    f"error:{definition.code}",
                ),
                policy_version=pinned_policy_version(),
            )
            publish_decision(decision)
            state_cursor[0] = decision.state
            recovery_events.append(decision.to_trace())
        append_recovery_event(
            recovery_events,
            {
                "event": "mcp.recovery_decided",
                "status": route.action,
                "agent": agent_name,
                "tool": tool_name,
                "error_code": definition.code,
                "retry_count": route.retry_count,
                "policy_version": pinned_policy_version(),
                "matched_rule_id": definition.code,
                "reason_code": f"RECOVERY_{route.action}",
                "reason": route.reason,
            },
        )
        if route.action == "RETRY":
            retry_count = route.retry_count
            await wait_before_retry(retry_count)
            continue
        if (
            route.action == "TOOL_FALLBACK"
            and route.target_tool == fallback_tool
            and fallback_operation is not None
        ):
            # Re-enter the same decision loop for fallback failures. The fallback
            # consumes the shared operation budget and cannot fall back recursively.
            tool_name = fallback_tool
            operation = fallback_operation
            fallback_tool = None
            fallback_operation = None
            retry_count = route.retry_count
            continue
        raise MCPRecoveryUnavailable(tool_name, error_type, recovery_events) from failure
