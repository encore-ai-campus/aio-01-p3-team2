"""게임 데이터에 근거해 이벤트 규칙을 결정적으로 검사한다."""

from collections.abc import Mapping
from dataclasses import dataclass


class GameDataError(ValueError):
    """규칙 판정에 필요한 게임 데이터가 없거나 형식이 잘못된 상태다."""


@dataclass(frozen=True)
class RuleCheck:
    """단일 게임 규칙의 판정과 근거를 보관한다.

    여러 검사의 종합 결과와 실제 데이터 버전·출처는 Balance & Rule 단계가
    A의 공통 ``RuleValidationResult`` 계약에 맞춰 구성한다.
    """

    is_valid: bool
    error_type: str | None
    reason: str
    evidence: dict[str, object]


class GameRuleEngine:
    """Event Brief, 미션, 보상을 LLM 판단 없이 Python 규칙으로 검사한다."""

    @staticmethod
    def validate_brief(brief: Mapping[str, object], plan: Mapping[str, object]) -> RuleCheck:
        """기획 결과의 대상과 기간이 원래 Event Brief와 같은지 검사한다."""
        for field in ("target_user", "duration"):
            if plan.get(field) != brief.get(field):
                return RuleCheck(
                    is_valid=False,
                    error_type="event_brief_mismatch",
                    reason=f"이벤트 기획안의 {field}가 원래 요청과 다릅니다.",
                    evidence={
                        "field": field,
                        "requested": brief.get(field),
                        "planned": plan.get(field),
                    },
                )
        return RuleCheck(True, None, "이벤트 대상과 기간이 원래 요청과 일치합니다.", {})

    @staticmethod
    def validate_day(day: int, duration: int, *, kind: str) -> RuleCheck:
        """Mission 또는 Reward의 Day가 이벤트 기간 안에 있는지 검사한다."""
        if kind not in {"mission", "reward"}:
            raise ValueError("kind는 mission 또는 reward여야 합니다.")
        if not 1 <= day <= duration:
            return RuleCheck(
                is_valid=False,
                error_type="event_brief_mismatch",
                reason=f"{kind}의 Day가 이벤트 기간을 벗어났습니다.",
                evidence={"kind": kind, "day": day, "duration": duration},
            )
        return RuleCheck(True, None, f"{kind}의 Day가 이벤트 기간 안에 있습니다.", {})

    @staticmethod
    def validate_content_access(
        content_id: str,
        target_user: str,
        day: int,
        content_rule: Mapping[str, object] | None,
        user_segments: Mapping[str, object],
    ) -> RuleCheck:
        """콘텐츠 존재 여부와 대상 사용자의 해당 Day 접근 가능성을 검사한다."""
        if content_rule is None:
            return RuleCheck(
                False,
                "mission_unreachable",
                "게임 데이터에 없는 콘텐츠입니다.",
                {"content_id": content_id, "day": day},
            )
        if content_rule.get("content_id") != content_id:
            raise GameDataError("조회한 콘텐츠 ID가 요청한 ID와 다릅니다.")
        if content_rule.get("exists") is False:
            return RuleCheck(
                False,
                "mission_unreachable",
                "게임 데이터에 없는 콘텐츠입니다.",
                {"content_id": content_id, "day": day},
            )
        if "exists" in content_rule and content_rule["exists"] is not True:
            raise GameDataError("콘텐츠 존재 여부 값이 올바르지 않습니다.")

        required_level = content_rule.get("required_level")
        if type(required_level) is not int or required_level < 1:
            raise GameDataError("콘텐츠 필요 레벨이 올바르지 않습니다.")

        segment = user_segments.get(target_user)
        if not isinstance(segment, Mapping):
            raise GameDataError("이벤트 대상의 성장 기준 데이터가 없습니다.")

        if target_user == "all_user":
            allowed_level = segment.get("common_access_max_required_level")
            if type(allowed_level) is not int or allowed_level < 1:
                raise GameDataError("전체 유저 공통 접근 레벨 기준이 올바르지 않습니다.")
            evidence = {
                "content_id": content_id,
                "day": day,
                "required_level": required_level,
                "common_access_max_required_level": allowed_level,
            }
        elif target_user in {"new_user", "returning_user"}:
            allowed_level = GameRuleEngine.expected_level_for_day(segment, day)
            evidence = {
                "content_id": content_id,
                "day": day,
                "required_level": required_level,
                "expected_level": allowed_level,
            }
        else:
            raise GameDataError("허용되지 않은 이벤트 대상입니다.")

        if required_level > allowed_level:
            return RuleCheck(
                False,
                "mission_unreachable",
                "대상 사용자가 해당 Day에 콘텐츠에 접근할 수 없습니다.",
                evidence,
            )
        return RuleCheck(True, None, "콘텐츠 레벨 조건을 충족합니다.", evidence)

    @staticmethod
    def expected_level_for_day(segment: Mapping[str, object], day: int) -> int:
        """해당 Day 이하의 가장 최근 Mock 성장 기준 레벨을 반환한다."""
        levels = segment.get("expected_levels")
        if not isinstance(levels, Mapping):
            raise GameDataError("예상 성장 레벨 데이터가 없습니다.")

        candidates: list[tuple[int, int]] = []
        for raw_day, raw_level in levels.items():
            if not isinstance(raw_day, str) or not raw_day.isdecimal():
                raise GameDataError("예상 레벨의 Day 키가 올바르지 않습니다.")
            level_day = int(raw_day)
            if level_day < 1 or type(raw_level) is not int or raw_level < 1:
                raise GameDataError("예상 성장 레벨 값이 올바르지 않습니다.")
            if level_day <= day:
                candidates.append((level_day, raw_level))

        if not candidates:
            raise GameDataError("해당 Day에 적용할 성장 기준이 없습니다.")
        return max(candidates)[1]

    @staticmethod
    def validate_item(item_id: str, item_info: Mapping[str, object] | None) -> RuleCheck:
        """보상 아이템이 실제 게임 데이터에 존재하는지 검사한다."""
        if item_info is None:
            return RuleCheck(
                False, "item_not_found", "게임 데이터에 없는 아이템입니다.", {"item_id": item_id}
            )
        if item_info.get("item_id") != item_id:
            raise GameDataError("조회한 아이템 ID가 요청한 ID와 다릅니다.")
        exists = item_info.get("exists")
        if type(exists) is not bool:
            raise GameDataError("아이템 존재 여부 값이 올바르지 않습니다.")
        if not exists:
            return RuleCheck(
                False, "item_not_found", "게임 데이터에 없는 아이템입니다.", {"item_id": item_id}
            )
        return RuleCheck(True, None, "보상 아이템이 존재합니다.", {"item_id": item_id})
