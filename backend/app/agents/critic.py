"""검증된 이벤트 기획안의 최종 기획 품질을 평가한다."""

from collections.abc import Mapping
from copy import deepcopy

from ..services.llm_service import LLMService


class CriticInputError(ValueError):
    """Critic에 전달된 요청이나 규칙 검증 결과가 올바르지 않은 상태다."""


class CriticOutputError(ValueError):
    """Critic의 구조화된 응답이 상태별 계약을 따르지 않은 상태다."""


class Critic:
    """규칙 검증을 통과한 기획안을 LLM으로 평가한다.

    기술 오류 복구와 다음 Agent 선택은 Supervisor가 담당한다.
    이 클래스는 기획 품질의 PASS, RETRY, FAIL만 반환한다.
    """

    AGENT_NAME = "critic_agent"
    _CONTEXT_KEYS = frozenset(
        {
            "task_id",
            "user_request",
            "event_plan",
            "validation_result",
            "error_type",
            "error_reason",
            "retry_count",
        }
    )
    _STATUSES = frozenset({"PASS", "RETRY", "FAIL"})

    def __init__(self, llm_service: LLMService) -> None:
        """Critic이 사용할 공통 LLM 호출 서비스를 보관한다."""
        self._llm_service = llm_service

    async def review(self, context: Mapping[str, object]) -> dict[str, object]:
        """검증된 기획안을 평가하고 CriticResult 형태의 응답을 반환한다.

        호출자는 Supervisor의 Context 필터를 먼저 적용해야 한다.
        A의 공통 CriticResult Validation은 Workflow에서 다시 수행한다.
        """
        if not isinstance(context, Mapping) or set(context) - self._CONTEXT_KEYS:
            raise CriticInputError("Critic Context에 허용되지 않은 값이 있습니다.")
        if not isinstance(context.get("user_request"), Mapping):
            raise CriticInputError("원래 Event Brief가 없습니다.")
        if not isinstance(context.get("event_plan"), Mapping):
            raise CriticInputError("검토할 이벤트 기획안이 없습니다.")

        validation = context.get("validation_result")
        if not isinstance(validation, Mapping) or validation.get("is_valid") is not True:
            raise CriticInputError("규칙 검증을 통과한 기획안만 검토할 수 있습니다.")

        result = await self._llm_service.generate(self.AGENT_NAME, deepcopy(dict(context)))
        status = result.get("status")
        error_type = result.get("error_type")
        reason = result.get("reason")

        if not isinstance(status, str) or status not in self._STATUSES:
            raise CriticOutputError("Critic 상태는 PASS, RETRY, FAIL 중 하나여야 합니다.")
        if not isinstance(reason, str) or not reason.strip():
            raise CriticOutputError("Critic 판정 이유가 필요합니다.")
        if status == "PASS" and error_type is not None:
            raise CriticOutputError("PASS에는 오류 유형을 지정할 수 없습니다.")
        if status == "RETRY" and (not isinstance(error_type, str) or not error_type):
            raise CriticOutputError("RETRY에는 오류 유형이 필요합니다.")
        if status == "FAIL" and error_type is not None and not isinstance(error_type, str):
            raise CriticOutputError("FAIL의 오류 유형이 올바르지 않습니다.")

        review_note = result.get("review_note")
        if review_note is not None and (
            not isinstance(review_note, str) or not review_note.strip()
        ):
            raise CriticOutputError("Critic 검토 메모가 올바르지 않습니다.")

        return {
            "status": status,
            "error_type": error_type,
            "reason": reason,
            **({"review_note": review_note} if review_note is not None else {}),
        }
