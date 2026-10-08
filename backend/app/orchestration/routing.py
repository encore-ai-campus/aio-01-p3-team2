"""표준 오류 유형에 따라 Workflow의 복구 경로를 결정한다."""

from dataclasses import dataclass
from typing import Literal

from .retry import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_REVISIONS,
    next_retry_count,
    next_revision_count,
)

RecoveryAction = Literal["RUN_AGENT", "RETRY", "TOOL_FALLBACK", "SAFE_STOP"]

_PLANNING_ERRORS = frozenset({"mission_unreachable", "item_not_found", "event_brief_mismatch"})
_AGENT_RETRY_ERRORS = frozenset({"agent_failure", "validation_error", "llm_timeout"})
_AGENT_NAMES = frozenset({"event_planner_agent", "balance_rule_agent", "critic_agent"})


@dataclass(frozen=True)
class RecoveryRoute:
    """복구 판단 결과와 다음 단계에 적용할 카운터를 보관한다.

    이 객체는 B 내부 판단 결과다. A의 공통 ``RoutingResult``가 확정되면
    Workflow Engine에서 해당 Schema로 변환해 검증한다.
    """

    action: RecoveryAction
    next_agent: str | None
    reason: str
    retry_count: int
    revision_count: int
    target_tool: str | None = None


def decide_recovery(
    error_type: str,
    *,
    current_agent: str | None = None,
    failed_tool: str | None = None,
    retry_count: int = 0,
    revision_count: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
) -> RecoveryRoute:
    """기획 오류와 기술 오류를 구분해 다음 복구 단계를 결정한다.

    이 함수는 Agent나 Tool을 실행하지 않고 카운터도 직접 저장하지 않는다.
    ``SAFE_STOP``이면 검증되지 않은 결과를 버리는 일은 Engine이 맡는다.
    """
    if error_type in _PLANNING_ERRORS:
        next_count = next_revision_count(revision_count, max_revisions)
        if next_count is None:
            return _safe_stop("자동 기획 수정 한도를 초과했습니다.", retry_count, revision_count)
        return RecoveryRoute(
            action="RUN_AGENT",
            next_agent="event_planner_agent",
            reason="원래 Event Brief를 기준으로 기획안을 수정하고 전체 재검증합니다.",
            retry_count=retry_count,
            revision_count=next_count,
        )

    if error_type in _AGENT_RETRY_ERRORS:
        if current_agent not in _AGENT_NAMES:
            return _safe_stop("재실행할 Agent를 확인할 수 없습니다.", retry_count, revision_count)
        return _retry(current_agent, retry_count, revision_count, max_retries)

    if error_type == "mcp_tool_error":
        if failed_tool == "get_game_rules":
            return RecoveryRoute(
                action="TOOL_FALLBACK",
                next_agent=None,
                reason="Primary Tool 오류로 검증된 Fallback Tool을 사용합니다.",
                retry_count=retry_count,
                revision_count=revision_count,
                target_tool="get_game_rules_fallback",
            )
        if failed_tool == "get_item_info":
            return _retry(None, retry_count, revision_count, max_retries, "get_item_info")
        if failed_tool == "get_event_planning_context":
            return _retry(None, retry_count, revision_count, max_retries, failed_tool)
        return _safe_stop("사용 가능한 Tool 대체 경로가 없습니다.", retry_count, revision_count)

    if error_type == "mcp_timeout":
        if failed_tool not in {
            "get_event_planning_context",
            "get_game_rules",
            "get_game_rules_fallback",
            "get_item_info",
        }:
            return _safe_stop("재호출할 Tool을 확인할 수 없습니다.", retry_count, revision_count)
        return _retry(None, retry_count, revision_count, max_retries, failed_tool)

    if error_type == "db_connection_error":
        return _retry(None, retry_count, revision_count, max_retries)

    return _safe_stop("자동 복구 대상이 아닌 오류입니다.", retry_count, revision_count)


def _retry(
    next_agent: str | None,
    retry_count: int,
    revision_count: int,
    max_retries: int,
    target_tool: str | None = None,
) -> RecoveryRoute:
    """기술 재시도 한도를 확인하고 동일 작업 재실행을 요청한다."""
    next_count = next_retry_count(retry_count, max_retries)
    if next_count is None:
        return _safe_stop("기술 재시도 한도를 초과했습니다.", retry_count, revision_count)
    return RecoveryRoute(
        action="RETRY",
        next_agent=next_agent,
        reason="마지막 검증 완료 상태에서 실패한 작업만 다시 실행합니다.",
        retry_count=next_count,
        revision_count=revision_count,
        target_tool=target_tool,
    )


def _safe_stop(reason: str, retry_count: int, revision_count: int) -> RecoveryRoute:
    """복구할 수 없는 상태를 다음 Agent가 없는 안전 종료로 표현한다."""
    return RecoveryRoute(
        action="SAFE_STOP",
        next_agent=None,
        reason=reason,
        retry_count=retry_count,
        revision_count=revision_count,
    )
