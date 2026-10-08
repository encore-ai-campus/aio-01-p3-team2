"""Agent Context 제한과 오류 복구 판단을 조정한다."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import ClassVar

from ..orchestration.routing import RecoveryRoute, decide_recovery
from ..services.llm_service import LLMExecutionError, LLMService, LLMTimeoutError


class ContextPolicyError(ValueError):
    """Agent Context 정책이 없거나 금지된 값을 허용할 때 발생한다."""


class SupervisorDecisionError(ValueError):
    """Supervisor가 정상 전환을 거부하거나 잘못된 판단을 반환한 상태다."""


class Supervisor:
    """정책에 맞는 Agent Context를 만들고 오류의 복구 경로를 선택한다.

    LLM이 정상 전환과 복구 경로를 심사한다. 허용된 복구 경로와 한도는
    결정적 정책이 정하며, LLM은 그 경로를 승인하거나 안전 종료시킨다.
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

    def __init__(
        self, security_policy: Mapping[str, object], llm_service: LLMService | None = None
    ) -> None:
        """파싱된 보안 정책에서 Agent별 Context 허용 목록을 확인한다."""
        context_access = security_policy.get("context_access")
        if not isinstance(context_access, Mapping):
            raise ContextPolicyError("보안 정책에 context_access가 필요합니다.")
        self._context_access = context_access
        self._llm_service = llm_service

    async def review_transition(
        self,
        action: str,
        next_agent: str | None,
        reason: str,
        decision_context: Mapping[str, object] | None = None,
    ) -> str:
        """LLM이 제안된 정상 단계 전환을 심사하고 Trace용 이유를 반환한다."""
        if self._llm_service is None:
            return reason
        judgment = await self._judge(
            {
                "kind": "normal_transition",
                "action": action,
                "next_agent": next_agent,
                "proposed_reason": reason,
                "decision_context": dict(decision_context or {}),
            }
        )
        if not judgment["approved"]:
            raise SupervisorDecisionError(judgment["reason"])
        source = "LLM" if self._llm_service.source == "live" else "Mock"
        return f"{source} 판단: {judgment['reason']}"

    async def review_failure(
        self,
        error_type: str,
        *,
        current_agent: str | None = None,
        failed_tool: str | None = None,
        retry_count: int = 0,
        revision_count: int = 0,
    ) -> RecoveryRoute:
        """정책상 가능한 복구안을 LLM이 심사하고 거부하면 안전 종료한다."""
        route = self.decide_failure(
            error_type,
            current_agent=current_agent,
            failed_tool=failed_tool,
            retry_count=retry_count,
            revision_count=revision_count,
        )
        if self._llm_service is None:
            return route
        try:
            judgment = await self._judge(
                {
                    "kind": "recovery",
                    "error_type": error_type,
                    "current_agent": current_agent,
                    "failed_tool": failed_tool,
                    "retry_count": retry_count,
                    "revision_count": revision_count,
                    "proposed_action": route.action,
                    "proposed_next_agent": route.next_agent,
                    "proposed_reason": route.reason,
                }
            )
        except (LLMExecutionError, LLMTimeoutError, SupervisorDecisionError):
            return replace(
                route,
                action="SAFE_STOP",
                next_agent=None,
                target_tool=None,
                retry_count=retry_count,
                revision_count=revision_count,
                reason="Supervisor 판단에 실패해 안전 종료합니다.",
            )
        if not judgment["approved"]:
            source = "LLM" if self._llm_service.source == "live" else "Mock"
            return replace(
                route,
                action="SAFE_STOP",
                next_agent=None,
                target_tool=None,
                retry_count=retry_count,
                revision_count=revision_count,
                reason=f"{source} 판단: {judgment['reason']}",
            )
        source = "LLM" if self._llm_service.source == "live" else "Mock"
        return replace(route, reason=f"{route.reason} ({source} 판단: {judgment['reason']})")

    async def _judge(self, context: Mapping[str, object]) -> dict[str, object]:
        """Supervisor의 제한된 판단 응답을 확인한다."""
        assert self._llm_service is not None
        result = await self._llm_service.generate("supervisor", context)
        approved = result.get("approved")
        reason = result.get("reason")
        if type(approved) is not bool or not isinstance(reason, str) or not reason.strip():
            raise SupervisorDecisionError("Supervisor LLM 응답 형식이 올바르지 않습니다.")
        return {"approved": approved, "reason": reason.strip()}

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
