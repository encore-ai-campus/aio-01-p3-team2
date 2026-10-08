"""Test Mode에서 주입할 수 있는 장애 유형을 정의한다."""

from enum import StrEnum


class FaultType(StrEnum):
    """API와 Workflow가 공통으로 사용하는 장애 주입 유형이다.

    값은 Test Mode 요청의 ``fault_type`` 문자열과 일치한다.
    ``_once`` 유형은 최초 한 번만 장애를 발생시키며,
    실제 주입 횟수 관리는 Fault Manager가 담당한다.
    """

    MISSION_UNREACHABLE_ONCE = "mission_unreachable_once"
    AGENT_FAILURE_ONCE = "agent_failure_once"
    PRIMARY_TOOL_ERROR_ONCE = "primary_tool_error_once"
    MCP_TIMEOUT_ONCE = "mcp_timeout_once"
    DB_CONNECTION_ERROR_ONCE = "db_connection_error_once"
    VALIDATION_ERROR_ONCE = "validation_error_once"
    RETRY_LIMIT_EXCEEDED = "retry_limit_exceeded"
