"""Agent Context 제한과 오류 복구 판단을 조정한다."""

from collections.abc import Mapping
from copy import deepcopy
from typing import ClassVar

from ..orchestration.routing import RecoveryRoute, decide_recovery


class ContextPolicyError(ValueError):
    """Agent Context 정책이 없거나 금지된 값을 허용할 때 발생한다."""


class Supervisor:
    """정책에 맞는 Agent Context를 만들고 오류의 복구 경로를 선택한다.

    Supervisor는 LLM을 호출하지 않는다. 정상 단계의 실행·상태 저장·Trace
    기록은 Workflow Engine이 담당하고, 이 클래스는 결정적 판단만 제공한다.
    """

    _BRIEF_KEYS: ClassVar[tuple[str, ...]] = (
        "event_name",
        "target_user",
        "duration",
        "objective",
    )
    _FORBIDDEN_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "actor_id",
            "idempotency_key",
            "fault_type",
            "api_key",
            "password",
            "access_token",
            "db_password",
            "docker_token",
            "ssh_private_key",
            "system_prompt",
            "full_prompt",
        }
    )

    def __init__(self, security_policy: Mapping[str, object]) -> None:
        """파싱된 보안 정책에서 Agent별 Context 허용 목록을 확인한다."""
        context_access = security_policy.get("context_access")
        if not isinstance(context_access, Mapping):
            raise ContextPolicyError("보안 정책에 context_access가 필요합니다.")
        self._context_access = context_access

    def build_agent_context(
        self, agent_name: str, state: Mapping[str, object]
    ) -> dict[str, object]:
        """허용된 State 필드만 복사해 Agent에게 전달할 Context를 만든다.

        ``user_request``에는 Event Brief의 네 필드만 남긴다. 원본 State는
        변경하지 않으며, 금지된 키가 정책에 포함되면 실행을 차단한다.
        """
        allowed_keys = self._context_access.get(agent_name)
        if not isinstance(allowed_keys, list) or not all(
            isinstance(key, str) for key in allowed_keys
        ):
            raise ContextPolicyError("Agent의 Context 허용 목록이 올바르지 않습니다.")

        if self._FORBIDDEN_KEYS.intersection(allowed_keys):
            raise ContextPolicyError("Agent Context 정책에 금지된 키가 포함되어 있습니다.")

        context = {key: deepcopy(state[key]) for key in allowed_keys if key in state}
        if "user_request" in context:
            request = context["user_request"]
            if not isinstance(request, Mapping) or any(
                key not in request for key in self._BRIEF_KEYS
            ):
                raise ContextPolicyError("Agent에게 전달할 Event Brief가 올바르지 않습니다.")
            context["user_request"] = {key: request[key] for key in self._BRIEF_KEYS}

        return context

    def decide_failure(
        self,
        error_type: str,
        *,
        current_agent: str | None = None,
        failed_tool: str | None = None,
        retry_count: int = 0,
        revision_count: int = 0,
    ) -> RecoveryRoute:
        """현재 오류를 Routing 규칙에 전달해 복구 또는 안전 종료를 선택한다."""
        return decide_recovery(
            error_type,
            current_agent=current_agent,
            failed_tool=failed_tool,
            retry_count=retry_count,
            revision_count=revision_count,
        )
