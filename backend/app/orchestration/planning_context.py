"""기획용 MCP 응답을 검증하고 Planner에게 필요한 정보만 선별한다."""

from collections.abc import Mapping

from ..services.mcp_client import MCPToolError

TOOL_NAME = "get_event_planning_context"
CHECKPOINT_DAYS = (1, 3, 5, 7)


def _invalid() -> MCPToolError:
    return MCPToolError(TOOL_NAME)


def validate_planning_context(
    result: Mapping[str, object], target_user: str, duration: int
) -> dict[str, object]:
    """확정된 MCP 응답 구조와 요청 일치를 검사한 뒤 필요한 필드만 남긴다."""
    if (
        result.get("target_user") != target_user
        or type(result.get("duration")) is not int
        or result["duration"] != duration
    ):
        raise _invalid()
    version = result.get("game_data_version")
    if not isinstance(version, str) or not version.strip() or result.get("source") != "primary":
        raise _invalid()

    contents = result.get("contents")
    items = result.get("items")
    segments = result.get("user_segments")
    if (
        not isinstance(contents, Mapping)
        or not contents
        or not isinstance(items, Mapping)
        or not items
        or not isinstance(segments, Mapping)
    ):
        raise _invalid()
    segment = segments.get(target_user)
    if not isinstance(segment, Mapping):
        raise _invalid()

    planning_data: dict[str, object] = {"game_data_version": version, "source": "primary"}
    if target_user == "all_user":
        access_level = segment.get("common_access_max_required_level")
        if segment.get("validation_policy") != "content_access_only" or (
            type(access_level) is not int or access_level < 1
        ):
            raise _invalid()
        planning_data["common_access_max_required_level"] = access_level
        max_level = access_level
    else:
        levels = segment.get("expected_levels")
        expected_days = {str(day) for day in CHECKPOINT_DAYS if day <= duration}
        if (
            segment.get("validation_policy") != "expected_level"
            or not isinstance(levels, Mapping)
            or set(levels) != expected_days
            or any(type(level) is not int or level < 1 for level in levels.values())
        ):
            raise _invalid()
        planning_data["daily_expected_levels"] = dict(levels)
        max_level = max(levels.values())

    available_contents = []
    for content_id, content in contents.items():
        if (
            not isinstance(content_id, str)
            or not content_id.strip()
            or not isinstance(content, Mapping)
            or not isinstance(content.get("content_name"), str)
            or not content["content_name"].strip()
            or type(content.get("required_level")) is not int
            or content["required_level"] < 1
        ):
            raise _invalid()
        if content["required_level"] <= max_level:
            available_contents.append(
                {"content_id": content_id, "required_level": content["required_level"]}
            )
    if not available_contents:
        raise _invalid()

    reward_item_ids = []
    for item_id, item in items.items():
        if (
            not isinstance(item_id, str)
            or not item_id.strip()
            or not isinstance(item, Mapping)
            or not isinstance(item.get("item_name"), str)
            or not item["item_name"].strip()
        ):
            raise _invalid()
        reward_item_ids.append(item_id)

    planning_data["available_contents"] = available_contents
    planning_data["reward_item_ids"] = reward_item_ids
    return planning_data
