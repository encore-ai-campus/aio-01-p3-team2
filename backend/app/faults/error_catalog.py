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
    "input_validation_error": ErrorDefinition(
        "ERR-INPUT-API-001", "INPUT", "API", "NEVER", "입력값을 확인해주세요."
    ),
    "input_guard_blocked": ErrorDefinition(
        "ERR-SEC-INPUT-001", "SECURITY", "API", "NEVER", "보안 정책에 의해 요청을 차단했습니다."
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
        "NEVER",
        "기획 Agent 결과 형식이 올바르지 않습니다.",
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
    "unknown_error": ErrorDefinition(
        "ERR-UNKNOWN-WORKFLOW-001",
        "INTERNAL",
        "WORKFLOW",
        "NEVER",
        "원인을 분류할 수 없어 안전 종료했습니다.",
    ),
}

LEGACY_ERROR_ALIASES: dict[str, str] = {
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
