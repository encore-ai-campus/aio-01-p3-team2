"""MCP Tool 호출 전에 Agent 권한과 인자를 검사한다."""

from collections.abc import Mapping
from typing import ClassVar


class ToolGuardError(PermissionError):
    """허용되지 않은 Tool 호출이나 잘못된 Tool 인자를 나타낸다."""


class ToolGuard:
    """Agent 설정을 기준으로 MCP Tool 호출을 허용하거나 차단한다.

    설정 파일을 읽고 파싱하는 일은 호출자가 담당한다. 이 클래스는
    파싱된 ``agents.yaml`` 데이터만 받아 호출 직전에 정책을 검사한다.
    """

    _STRING_KEYS: ClassVar[dict[str, str]] = {
        "get_event_capabilities": "game_id",
        "get_event_planning_context": "target_user",
        "get_game_rules": "content_id",
        "get_game_rules_fallback": "content_id",
        "get_item_info": "item_id",
    }

    def __init__(self, agents_config: Mapping[str, object]) -> None:
        """Agent 설정에서 ``workers`` 목록을 확인하고 보관한다."""
        workers = agents_config.get("workers")
        if not isinstance(workers, Mapping):
            raise TypeError("Agent 설정에 workers가 필요합니다.")
        self._workers = workers

    def validate_call(
        self,
        agent_name: str,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        primary_failed: bool = False,
    ) -> None:
        """Agent 권한, Tool 인자, Fallback 호출 조건을 확인한다.

        허용된 호출이면 아무 값도 반환하지 않는다. 정책을 위반하면
        ``ToolGuardError``를 발생시켜 MCP 호출 전에 중단한다.
        """
        worker = self._workers.get(agent_name)
        if not isinstance(worker, Mapping):
            raise ToolGuardError("알 수 없는 Agent의 Tool 호출입니다.")

        allowed_tools = worker.get("allowed_tools")
        if not isinstance(allowed_tools, list) or not all(
            isinstance(name, str) for name in allowed_tools
        ):
            raise ToolGuardError("Agent의 Tool 허용 목록이 올바르지 않습니다.")

        if tool_name not in allowed_tools:
            raise ToolGuardError("Agent에게 허용되지 않은 Tool입니다.")

        expected_key = self._STRING_KEYS.get(tool_name)
        if expected_key is None:
            raise ToolGuardError("등록되지 않은 Tool입니다.")

        if tool_name == "get_game_rules_fallback" and not primary_failed:
            raise ToolGuardError("Primary Tool 실패 없이 Fallback을 호출할 수 없습니다.")

        allowed_keys = {
            "get_event_capabilities": (
                {"game_id", "game_data_snapshot_id"},
                {"game_id", "game_data_snapshot_id", "capability_catalog_version"},
            ),
            "get_event_planning_context": (
                {"target_user", "duration"},
                {"game_id", "game_data_snapshot_id", "target_segment_id", "duration_days"},
            ),
            "get_game_rules": ({"content_id"}, {"content_id", "game_data_snapshot_id"}),
            "get_game_rules_fallback": (
                {"content_id"},
                {"content_id", "game_data_snapshot_id", "expected_snapshot_digest"},
            ),
            "get_item_info": ({"item_id"}, {"item_id", "game_data_snapshot_id"}),
        }[tool_name]
        if not isinstance(arguments, Mapping) or set(arguments) not in allowed_keys:
            raise ToolGuardError("Tool 인자 이름이 올바르지 않습니다.")

        if any(
            not isinstance(arguments.get(key), str) or not arguments[key].strip()
            for key in set(arguments) - {"duration", "duration_days"}
        ):
            raise ToolGuardError("Tool 인자는 비어 있지 않은 문자열이어야 합니다.")
        if tool_name == "get_event_planning_context":
            if "target_user" in arguments and arguments["target_user"] not in {
                "new_user",
                "returning_user",
                "all_user",
            }:
                raise ToolGuardError("이벤트 대상이 올바르지 않습니다.")
            if "target_segment_id" in arguments and arguments["game_id"] != "opong_heroes":
                raise ToolGuardError("지원하지 않는 게임 ID입니다.")
            duration = arguments.get("duration", arguments.get("duration_days"))
            if type(duration) is not int or not 1 <= duration <= 7:
                raise ToolGuardError("이벤트 기간은 1~7일이어야 합니다.")
        if tool_name == "get_event_capabilities" and arguments["game_id"] != "opong_heroes":
            raise ToolGuardError("지원하지 않는 게임 ID입니다.")
