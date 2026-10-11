"""Stable error IDs and legacy aliases for planning Workflow occurrences."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDefinition:
    """One immutable cause/location identifier and its retry classification."""

    code: str
    category: str
    location: str
    retry_class: str
    safe_message: str


ERROR_CATALOG: dict[str, ErrorDefinition] = {
    "mcp_response_invalid": ErrorDefinition(
        "ERR-CONTRACT-MCP-001",
        "CONTRACT",
        "MCP",
        "NEVER",
        "게임 데이터 응답 계약이 올바르지 않아 실행을 중단했습니다.",
    ),
    "context_guard_blocked": ErrorDefinition(
        "ERR-SEC-CONTEXT-001",
        "SECURITY",
        "CONTEXT",
        "NEVER",
        "Agent 컨텍스트 보안 계약에 의해 실행을 중단했습니다.",
    ),
    "planner_explanation_mismatch": ErrorDefinition(
        "ERR-DOMAIN-PLANNER-002",
        "DOMAIN",
        "PLANNER",
        "PLAN",
        "기획 설명이 고정된 요청·게임 규칙과 맞지 않습니다.",
    ),
    "input_validation_error": ErrorDefinition(
        "ERR-INPUT-API-001", "INPUT", "API", "NEVER", "입력값을 확인해주세요."
    ),
    "input_guard_blocked": ErrorDefinition(
        "ERR-SEC-INPUT-001", "SECURITY", "API", "NEVER", "보안 정책에 의해 요청을 차단했습니다."
    ),
    "response_guard_blocked": ErrorDefinition(
        "ERR-SEC-OUTPUT-001",
        "SECURITY",
        "OUTPUT",
        "NEVER",
        "응답 보안 정책에 의해 실행을 중단했습니다.",
    ),
    "tool_permission_denied": ErrorDefinition(
        "ERR-SEC-TOOL-001", "SECURITY", "TOOL", "NEVER", "허용되지 않은 도구 호출입니다."
    ),
    "unsupported_capability": ErrorDefinition(
        "ERR-SCOPE-CHECK-001",
        "SCOPE",
        "SCOPE",
        "NEVER",
        "현재 Runtime에서 지원하지 않는 기능입니다.",
    ),
    "game_data_unavailable": ErrorDefinition(
        "ERR-DATA-CONTEXT-001", "DATA", "SCOPE", "NEVER", "필요한 게임 데이터가 없습니다."
    ),
    "planner_output_invalid": ErrorDefinition(
        "ERR-CONTRACT-PLANNER-001",
        "CONTRACT",
        "PLANNER",
        "AGENT",
        "기획 Agent 결과 형식이 올바르지 않습니다.",
    ),
    "critic_output_invalid": ErrorDefinition(
        "ERR-CONTRACT-CRITIC-001",
        "CONTRACT",
        "CRITIC",
        "AGENT",
        "검수 Agent 결과 형식이 올바르지 않습니다.",
    ),
    "handoff_contract_invalid": ErrorDefinition(
        "ERR-CONTRACT-HANDOFF-001",
        "CONTRACT",
        "HANDOFF",
        "NEVER",
        "Agent 인계 계약을 확인할 수 없습니다.",
    ),
    "event_brief_mismatch": ErrorDefinition(
        "ERR-DOMAIN-PLANNER-001",
        "DOMAIN",
        "PLANNER",
        "PLAN",
        "기획안이 요청 조건과 일치하지 않습니다.",
    ),
    "mission_unreachable": ErrorDefinition(
        "ERR-DOMAIN-RULE-001",
        "DOMAIN",
        "RULE",
        "PLAN",
        "미션 목표를 게임 규칙에서 달성할 수 없습니다.",
    ),
    "item_not_found": ErrorDefinition(
        "ERR-DOMAIN-REWARD-001",
        "DOMAIN",
        "REWARD",
        "PLAN",
        "보상 아이템 또는 지급 조건을 확인할 수 없습니다.",
    ),
    "llm_timeout": ErrorDefinition(
        "ERR-DEP-LLM-001", "DEPENDENCY", "LLM", "AGENT", "AI 응답 시간이 초과되었습니다."
    ),
    "llm_execution_error": ErrorDefinition(
        "ERR-DEP-LLM-002", "DEPENDENCY", "LLM", "NEVER", "AI 설정 또는 호출을 확인할 수 없습니다."
    ),
    "mcp_timeout": ErrorDefinition(
        "ERR-DEP-MCP-001",
        "DEPENDENCY",
        "MCP",
        "OPERATION",
        "게임 데이터 조회 시간이 초과되었습니다.",
    ),
    "mcp_tool_error": ErrorDefinition(
        "ERR-DEP-MCP-002",
        "DEPENDENCY",
        "MCP",
        "OPERATION",
        "게임 데이터 도구 응답을 확인할 수 없습니다.",
    ),
    "mcp_game_rules_error": ErrorDefinition(
        "ERR-DEP-MCP-003", "DEPENDENCY", "MCP", "OPERATION", "게임 규칙 조회를 완료할 수 없습니다."
    ),
    "db_connection_error": ErrorDefinition(
        "ERR-STORE-DB-001", "STORAGE", "DB", "OPERATION", "저장소 연결을 확인할 수 없습니다."
    ),
    "db_write_outcome_unknown": ErrorDefinition(
        "ERR-STORE-DB-002",
        "STORAGE",
        "DB",
        "NEVER",
        "저장 결과를 확인할 수 없어 재실행하지 않았습니다.",
    ),
    "trace_write_error": ErrorDefinition(
        "ERR-OBS-TRACE-001", "STORAGE", "TRACE", "NEVER", "필수 실행 기록을 저장할 수 없습니다."
    ),
    "recovery_limit_exceeded": ErrorDefinition(
        "ERR-CTRL-LIMIT-001", "CONTROL", "WORKFLOW", "NEVER", "복구 실행 한도를 초과했습니다."
    ),
    "supervisor_state_conflict": ErrorDefinition(
        "ERR-CTRL-STATE-001",
        "CONTROL",
        "WORKFLOW",
        "NEVER",
        "Workflow 상태가 다른 실행에서 갱신되어 안전 종료했습니다. 최신 상태를 확인해주세요.",
    ),
    "llm_budget_exceeded": ErrorDefinition(
        "ERR-CTRL-BUDGET-001",
        "CONTROL",
        "LLM",
        "NEVER",
        "LLM 호출 예산을 모두 사용해 안전 종료했습니다.",
    ),
    "workflow_deadline_exceeded": ErrorDefinition(
        "ERR-CTRL-DEADLINE-001",
        "CONTROL",
        "WORKFLOW",
        "NEVER",
        "Workflow 전체 실행 제한 시간을 초과해 안전 종료했습니다.",
    ),
    "unknown_error": ErrorDefinition(
        "ERR-UNKNOWN-WORKFLOW-001",
        "INTERNAL",
        "WORKFLOW",
        "NEVER",
        "원인을 분류할 수 없어 안전 종료했습니다.",
    ),
}

LEGACY_ERROR_ALIASES: dict[str, str] = {
    "llm_transient_error": "llm_timeout",
    "validation_error": "planner_output_invalid",
    "agent_failure": "llm_execution_error",
    "mission_unreachable": "mission_unreachable",
    "item_not_found": "item_not_found",
    "event_brief_mismatch": "event_brief_mismatch",
    "mcp_timeout": "mcp_timeout",
    "mcp_tool_error": "mcp_tool_error",
    "db_connection_error": "db_connection_error",
    "input_guard_blocked": "input_guard_blocked",
}


def error_definition(legacy_type: str | None) -> ErrorDefinition:
    """Resolve a legacy type to the stable code; fail closed for unknown types."""
    key = LEGACY_ERROR_ALIASES.get(legacy_type or "", legacy_type or "unknown_error")
    return ERROR_CATALOG.get(key, ERROR_CATALOG["unknown_error"])


def classify_workflow_exception(exc: Exception) -> str:
    """Normalize known boundary errors without attributing unknown failures to LLM."""
    from backend.app.agents.supervisor import ContextPolicyError
    from backend.app.guards.input_guard import InputGuardBlocked
    from backend.app.guards.tool_guard import ToolGuardError
    from backend.app.schemas.workflow_state import SupervisorStateConflict
    from backend.app.services.llm_service import (
        LLMBudgetExceeded,
        LLMExecutionError,
        LLMTimeoutError,
    )
    from backend.app.services.unit_of_work import DatabaseOutcomeUnknown
    from sqlalchemy.exc import SQLAlchemyError

    explicit = getattr(exc, "error_type", None)
    if isinstance(explicit, str) and (
        explicit in ERROR_CATALOG or explicit in LEGACY_ERROR_ALIASES
    ):
        return explicit
    if isinstance(exc, InputGuardBlocked):
        return "input_guard_blocked"
    if isinstance(exc, ContextPolicyError):
        return "context_guard_blocked"
    if isinstance(exc, ToolGuardError):
        return "tool_permission_denied"
    if isinstance(exc, DatabaseOutcomeUnknown):
        return "db_write_outcome_unknown"
    if isinstance(exc, SupervisorStateConflict):
        return "supervisor_state_conflict"
    if isinstance(exc, SQLAlchemyError):
        return "db_connection_error"
    if isinstance(exc, LLMTimeoutError):
        return "llm_timeout"
    if isinstance(exc, LLMBudgetExceeded):
        return "llm_budget_exceeded"
    if isinstance(exc, LLMExecutionError):
        return "llm_execution_error"
    return "unknown_error"
