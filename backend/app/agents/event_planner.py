"""Event Brief를 바탕으로 이벤트 초안 또는 수정안을 생성한다."""

from collections.abc import Mapping
from copy import deepcopy

from ..services.llm_service import LLMService


class PlannerInputError(ValueError):
    """Planner에 필요한 원래 요청 또는 Context가 올바르지 않은 상태다."""


class EventPlanner:
    """허용된 Context를 LLM에 전달하고 구조화된 기획안을 받는다.

    호출자는 Supervisor의 Context 필터를 먼저 적용해야 한다. 반환값은
    A의 EventPlanResult Output Validation을 통과한 뒤에만 사용한다.
    """

    AGENT_NAME = "event_planner_agent"
    _BRIEF_KEYS = ("event_name", "target_user", "duration", "objective")
    _CONTEXT_KEYS = frozenset(
        {
            "user_request",
            "event_plan",
            "error_type",
            "error_reason",
            "approval_comment",
            "revision_count",
        }
    )

    def __init__(self, llm_service: LLMService) -> None:
        """Planner가 사용할 공통 LLM 호출 서비스를 보관한다."""
        self._llm_service = llm_service

    async def plan(self, context: Mapping[str, object]) -> dict[str, object]:
        """원래 Event Brief를 포함한 Context로 초안 또는 수정안을 요청한다.

        이전 오류와 승인 의견은 전달받은 경우에만 포함한다. 계획 결과의
        대상·기간 일치와 게임 규칙은 후속 Python Rule Engine이 검사한다.
        """
        if not isinstance(context, Mapping) or set(context) - self._CONTEXT_KEYS:
            raise PlannerInputError("Planner Context에 허용되지 않은 값이 있습니다.")

        brief = context.get("user_request")
        if not isinstance(brief, Mapping) or set(brief) != set(self._BRIEF_KEYS):
            raise PlannerInputError("Event Brief의 필드가 올바르지 않습니다.")
        if any(brief[key] is None for key in self._BRIEF_KEYS):
            raise PlannerInputError("Event Brief에 비어 있는 필드가 있습니다.")

        return await self._llm_service.generate(self.AGENT_NAME, deepcopy(dict(context)))
