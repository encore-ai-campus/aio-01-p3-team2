"""Agent Context 제한과 오류 복구 판단을 조정한다."""

from collections.abc import Mapping
from copy import deepcopy
from typing import ClassVar

from ..orchestration.routing import RecoveryRoute, decide_recovery


class ContextPolicyError(ValueError):
    """Agent Context 정책이 없거나 금지된 값을 허용할 때 발생한다."""


class SupervisorDecisionError(ValueError):
    """Supervisor가 정상 전환을 거부하거나 잘못된 판단을 반환한 상태다."""


class Supervisor:
    """정책과 Workflow 상태에 따라 결정적으로 실행 경로를 선택한다."""

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

    def __init__(
        self, security_policy: Mapping[str, object], llm_service: object | None = None
    ) -> None:
        """정책 파일의 호환 인자를 무시하고 Agent Context allowlist를 확인한다."""
        del llm_service  # Supervisor is deliberately excluded from all LLM calls.
        context_access = security_policy.get("context_access")
        if not isinstance(context_access, Mapping):
            raise ContextPolicyError("보안 정책에 context_access가 필요합니다.")
        self._context_access = context_access

    async def review_transition(
        self,
        action: str,
        next_agent: str | None,
        reason: str,
        decision_context: Mapping[str, object] | None = None,
    ) -> str:
        """허용된 상태 전이만 받아 원래의 결정 근거를 Trace에 남긴다."""
        del decision_context
        actions = {"RUN_AGENT", "WAIT_APPROVAL", "RETRY", "TOOL_FALLBACK", "SAFE_STOP"}
        agents = {"event_planner_agent", "balance_rule_agent", "critic_agent"}
        if action not in actions or not isinstance(reason, str) or not reason.strip():
            raise SupervisorDecisionError("Workflow 전이 제안이 올바르지 않습니다.")
        if action == "RUN_AGENT" and next_agent not in agents:
            raise SupervisorDecisionError("허용되지 않은 다음 Agent입니다.")
        if action != "RUN_AGENT" and next_agent is not None:
            raise SupervisorDecisionError("현재 전이는 다음 Agent를 지정할 수 없습니다.")
        return reason.strip()

    async def review_failure(
        self,
        error_type: str,
        *,
        current_agent: str | None = None,
        failed_tool: str | None = None,
        retry_count: int = 0,
        revision_count: int = 0,
    ) -> RecoveryRoute:
        """오류 taxonomy와 hard cap에 따라 복구 경로를 결정한다."""
        return self.decide_failure(
            error_type,
            current_agent=current_agent,
            failed_tool=failed_tool,
            retry_count=retry_count,
            revision_count=revision_count,
        )

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
