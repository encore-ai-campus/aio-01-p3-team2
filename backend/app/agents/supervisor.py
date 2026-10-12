"""Agent Context 제한과 오류 복구 판단을 조정한다."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import ClassVar
from uuid import uuid4

from ..orchestration.routing import RecoveryRoute, decide_recovery


class ContextPolicyError(ValueError):
    """Agent Context 정책이 없거나 금지된 값을 허용할 때 발생한다."""


class SupervisorDecisionError(ValueError):
    """Supervisor가 정상 전환을 거부하거나 잘못된 판단을 반환한 상태다."""


@dataclass(frozen=True)
class SupervisorWorkflowState:
    """A versioned workflow snapshot whose transitions are decided by Supervisor."""

    workflow_id: str
    status: str
    state_version: int = 0
    retry_count: int = 0
    revision_count: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SupervisorWorkflowState":
        try:
            state = cls(
                workflow_id=str(value["workflow_id"]),
                status=str(value["status"]),
                state_version=int(value["state_version"]),
                retry_count=int(value.get("retry_count", 0)),
                revision_count=int(value.get("revision_count", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SupervisorDecisionError("Workflow 상태 snapshot이 올바르지 않습니다.") from exc
        if (
            not state.workflow_id
            or min(state.state_version, state.retry_count, state.revision_count) < 0
        ):
            raise SupervisorDecisionError("Workflow 상태 version 또는 counter가 올바르지 않습니다.")
        return state

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SupervisorTransition:
    """A deterministic next-state decision and its auditable basis."""

    state: SupervisorWorkflowState
    from_state: str
    decision_id: str
    event: str
    action: str
    reason_code: str
    reason: str
    policy_version: int | None
    matched_rule_id: str
    error_code: str | None
    evidence_refs: tuple[str, ...]

    def to_trace(self) -> dict[str, object]:
        return {
            "event": "supervisor.state_transition_decided",
            "status": self.state.status,
            "decision_id": self.decision_id,
            "workflow_id": self.state.workflow_id,
            "state_version": self.state.state_version,
            "expected_state_version": self.state.state_version - 1,
            "from_state": self.from_state,
            "to_state": self.state.status,
            "workflow_event": self.event,
            "action": self.action,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "matched_rule_id": self.matched_rule_id,
            "error_code": self.error_code,
            "evidence_refs": list(self.evidence_refs),
            "retry_count": self.state.retry_count,
            "revision_count": self.state.revision_count,
        }


class Supervisor:
    """정책과 Workflow 상태에 따라 결정적으로 실행 경로를 선택한다."""

    @staticmethod
    def reconcile_critic_review(
        output: Mapping[str, object], plan: Mapping[str, object]
    ) -> dict[str, object]:
        """Reconcile only duplicate scope findings; retain independent semantic reviews."""
        result = deepcopy(dict(output))
        issues = result.get("findings")
        if (
            result.get("status") != "REVIEW"
            or plan.get("runtime_eligible") is not False
            or plan.get("plan_status") not in {"VALIDATED_WITH_DEVELOPMENT_ITEMS", "NEEDS_DATA"}
            or not isinstance(issues, list)
            or not issues
        ):
            return result
        summary = plan.get("validation_summary")
        known = summary.get("findings", []) if isinstance(summary, Mapping) else []
        blockers = [row for row in known if isinstance(row, Mapping) and row.get("blocks_runtime")]
        if not blockers or any(
            row.get("finding_type") != "SCOPE"
            or row.get("repairability") not in {"NEEDS_DEVELOPMENT", "NEEDS_DATA"}
            for row in blockers
        ):
            return result

        def field_path(value: object) -> str:
            path = str(value or "")
            for prefix in ("plan.", "brief."):
                path = path.removeprefix(prefix)
            return path

        paths = [field_path(row.get("location", {}).get("field_path")) for row in blockers]
        for issue in issues:
            if not isinstance(issue, Mapping) or issue.get("kind") != "SCOPE":
                return result
            path = field_path(issue.get("field_path"))
            if not any(
                root and (path == root or path.startswith((root + ".", root + "[")))
                for root in paths
            ):
                return result
        result.update(
            reported_status="REVIEW",
            status="PASS",
            review_resolution="EXISTING_SCOPE_FINDINGS",
            matched_scope_codes=[row["code"] for row in blockers],
        )
        return result

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
    _TERMINAL_STATES: ClassVar[frozenset[str]] = frozenset(
        {"COMPLETED", "REJECTED", "CANCELLED", "SAFE_STOP", "APPLIED", "REVISION_REQUESTED"}
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
        agents = {
            "event_planner_agent",
            "balance_rule_agent",
            "critic_agent",
            "canonical_event_planner_agent",
            "canonical_event_critic_agent",
        }
        if action not in actions or not isinstance(reason, str) or not reason.strip():
            raise SupervisorDecisionError("Workflow 전이 제안이 올바르지 않습니다.")
        if action == "RUN_AGENT" and next_agent not in agents:
            raise SupervisorDecisionError("허용되지 않은 다음 Agent입니다.")
        if action != "RUN_AGENT" and next_agent is not None:
            raise SupervisorDecisionError("현재 전이는 다음 Agent를 지정할 수 없습니다.")
        return reason.strip()

    def start_workflow(self, workflow_id: str) -> SupervisorWorkflowState:
        """Create the initial state snapshot for one accepted Workflow."""
        if not workflow_id.strip():
            raise SupervisorDecisionError("Workflow ID가 필요합니다.")
        return SupervisorWorkflowState(workflow_id=workflow_id, status="RECEIVED")

    def decide_legacy_engine_transition(
        self,
        state: SupervisorWorkflowState,
        action: str,
        next_agent: str | None,
        *,
        evidence_refs: tuple[str, ...] = (),
        policy_version: int | None = None,
    ) -> SupervisorTransition:
        """Map the legacy Task API's route command into the shared Workflow reducer."""
        if action == "RUN_AGENT":
            if next_agent == "event_planner_agent":
                if state.status == "RECEIVED":
                    return self.decide_state_transition(
                        state,
                        "legacy_request_accepted",
                        payload={"reason": "legacy Task request validated"},
                        evidence_refs=evidence_refs,
                        policy_version=policy_version,
                    )
                if state.status == "PLANNING":
                    return self.decide_state_transition(
                        state,
                        "planner_restarted",
                        payload={"reason": "revision execution started"},
                        evidence_refs=evidence_refs,
                        policy_version=policy_version,
                    )
                if state.status == "COLLECTING_EVIDENCE":
                    return self.decide_state_transition(
                        state,
                        "evidence_collected",
                        evidence_refs=evidence_refs,
                        policy_version=policy_version,
                    )
                if state.status in {"VALIDATING", "CRITIC_REVIEW"}:
                    return self.decide_state_transition(
                        state,
                        "revision_requested",
                        payload={"reason": "validation feedback"},
                        evidence_refs=evidence_refs,
                        policy_version=policy_version,
                    )
            if next_agent == "balance_rule_agent":
                return self.decide_state_transition(
                    state,
                    "planner_completed",
                    evidence_refs=evidence_refs,
                    policy_version=policy_version,
                )
            if next_agent == "critic_agent":
                return self.decide_state_transition(
                    state,
                    "validation_completed",
                    evidence_refs=evidence_refs,
                    policy_version=policy_version,
                )
        if action == "WAIT_APPROVAL":
            return self.decide_state_transition(
                state,
                "plan_reviewed",
                payload={"critic_status": "PASS", "runtime_eligible": True},
                evidence_refs=evidence_refs,
                policy_version=policy_version,
            )
        raise SupervisorDecisionError("기존 Task 경로의 상태 전이를 해석할 수 없습니다.")

    @staticmethod
    def issue_brief_business_ids(brief: Mapping[str, object]) -> dict[str, object]:
        """Issue authoritative action/reward IDs and resolve input links by position."""
        normalized = deepcopy(dict(brief))
        actions = normalized.get("actions")
        rewards = normalized.get("rewards")
        links = normalized.get("mission_reward_links")
        if (
            not isinstance(actions, list)
            or not isinstance(rewards, list)
            or not isinstance(links, list)
        ):
            raise SupervisorDecisionError("Brief의 미션·보상·연결 목록이 올바르지 않습니다.")
        action_indexes: dict[object, int] = {}
        reward_indexes: dict[object, int] = {}
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise SupervisorDecisionError("Brief 미션 항목이 올바르지 않습니다.")
            old_id = action.get("action_id")
            if old_id is not None:
                action_indexes[old_id] = index
            action["action_id"] = f"ACT-{uuid4().hex[:16].upper()}"
        for index, reward in enumerate(rewards):
            if not isinstance(reward, dict):
                raise SupervisorDecisionError("Brief 보상 항목이 올바르지 않습니다.")
            old_id = reward.get("reward_id")
            if old_id is not None:
                reward_indexes[old_id] = index
            reward["reward_id"] = f"REW-{uuid4().hex[:16].upper()}"
        resolved_links = []
        for link in links:
            if not isinstance(link, Mapping):
                raise SupervisorDecisionError("Brief 미션·보상 연결이 올바르지 않습니다.")
            if link.get("action_index") is not None and link.get("reward_index") is not None:
                action_index = link["action_index"]
                reward_index = link["reward_index"]
            else:
                action_index = action_indexes.get(link.get("action_id"))
                reward_index = reward_indexes.get(link.get("reward_id"))
            if (
                type(action_index) is not int
                or type(reward_index) is not int
                or not 0 <= action_index < len(actions)
                or not 0 <= reward_index < len(rewards)
            ):
                raise SupervisorDecisionError("Brief 연결이 미션·보상 항목을 참조하지 않습니다.")
            action_id = actions[action_index]["action_id"]
            reward_id = rewards[reward_index]["reward_id"]
            resolved_links.append({"action_id": action_id, "reward_id": reward_id})
        normalized["mission_reward_links"] = resolved_links
        return normalized

    def decide_state_transition(
        self,
        state: SupervisorWorkflowState,
        event: str,
        *,
        payload: Mapping[str, object] | None = None,
        error_code: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        policy_version: int | None = None,
    ) -> SupervisorTransition:
        """Return the only allowed next state for a validated Workflow event."""
        data = payload or {}
        human_revision = event == "approval_revision_requested"
        if human_revision and state.status not in {
            "WAITING_APPROVAL",
            "WAITING_REVIEW",
            "COMPLETED",
        }:
            raise SupervisorDecisionError("현재 상태에서 사용자 수정을 요청할 수 없습니다.")
        if state.status in self._TERMINAL_STATES and not human_revision:
            raise SupervisorDecisionError("Terminal Workflow 상태에서는 전이할 수 없습니다.")

        fixed = {
            "legacy_request_accepted": ("RECEIVED", "PLANNING", "RUN_PLANNER"),
            "request_validated": ("RECEIVED", "COLLECTING_EVIDENCE", "CONTINUE"),
            "evidence_collected": ("COLLECTING_EVIDENCE", "PLANNING", "RUN_PLANNER"),
            "planner_completed": ("PLANNING", "VALIDATING", "RUN_VALIDATORS"),
            "validation_completed": ("VALIDATING", "CRITIC_REVIEW", "RUN_CRITIC"),
            "approval_accepted": ("WAITING_APPROVAL", "APPROVED", "CONTINUE"),
            "approval_rejected": ("WAITING_APPROVAL", "REJECTED", "COMPLETE"),
            "approval_revision_requested": (state.status, "REVISION_REQUESTED", "COMPLETE"),
            "runtime_applied": ("APPROVED", "APPLIED", "COMPLETE"),
            "legacy_approval_accepted": ("WAITING_APPROVAL", "COMPLETED", "COMPLETE"),
            "legacy_approval_rejected": ("WAITING_APPROVAL", "CANCELLED", "COMPLETE"),
            "legacy_revision_requested": ("WAITING_APPROVAL", "PLANNING", "RUN_PLANNER"),
            "planner_restarted": ("PLANNING", "PLANNING", "RUN_PLANNER"),
            "cancelled": (state.status, "CANCELLED", "COMPLETE"),
            "safe_stop": (state.status, "SAFE_STOP", "STOP"),
            "revision_requested": (state.status, "PLANNING", "RUN_PLANNER"),
        }
        if event == "revision_requested" and state.status not in {"VALIDATING", "CRITIC_REVIEW"}:
            raise SupervisorDecisionError("현재 상태에서 Planner 수정을 요청할 수 없습니다.")
        if event == "plan_reviewed":
            expected = ("CRITIC_REVIEW",)
            critic_status = data.get("critic_status")
            eligible = data.get("runtime_eligible")
            if len(expected) != 1 or state.status != expected[0]:
                raise SupervisorDecisionError("현재 상태에서 Plan 검수 결과를 받을 수 없습니다.")
            if critic_status == "REVIEW":
                next_state, action = "WAITING_REVIEW", "WAIT_REVIEW"
            elif critic_status == "PASS" and eligible is True:
                next_state, action = "WAITING_APPROVAL", "WAIT_APPROVAL"
            elif critic_status == "PASS" and eligible is False:
                next_state, action = "COMPLETED", "COMPLETE"
            else:
                raise SupervisorDecisionError(
                    "Critic 또는 Runtime eligibility 결과가 올바르지 않습니다."
                )
            expected_state = "CRITIC_REVIEW"
        else:
            transition = fixed.get(event)
            if transition is None:
                raise SupervisorDecisionError("등록되지 않은 Workflow 이벤트입니다.")
            expected_state, next_state, action = transition
            if event in {"cancelled", "safe_stop"}:
                expected_state = state.status
            if state.status != expected_state:
                raise SupervisorDecisionError(
                    f"현재 상태 {state.status}에서 {event} 이벤트를 처리할 수 없습니다."
                )

        next_state_snapshot = SupervisorWorkflowState(
            workflow_id=state.workflow_id,
            status=next_state,
            state_version=state.state_version + 1,
            retry_count=state.retry_count,
            revision_count=(
                state.revision_count + 1
                if event == "revision_requested" and not data.get("recovery_count_recorded")
                else state.revision_count
            ),
        )
        reason_code = f"WORKFLOW_{event.upper().replace('-', '_')}"
        return SupervisorTransition(
            state=next_state_snapshot,
            from_state=state.status,
            decision_id=str(uuid4()),
            event=event,
            action=action,
            reason_code=reason_code,
            reason=str(data.get("reason", event.replace("_", " "))),
            policy_version=policy_version,
            matched_rule_id=f"workflow.transition.{event}",
            error_code=error_code,
            evidence_refs=tuple(ref for ref in evidence_refs if ref),
        )

    def record_recovery_decision(
        self,
        state: SupervisorWorkflowState,
        route: RecoveryRoute,
        *,
        error_code: str,
        evidence_refs: tuple[str, ...] = (),
        policy_version: int | None = None,
    ) -> SupervisorTransition:
        """Record policy-selected retry/stop while keeping status ownership here."""
        if state.status in self._TERMINAL_STATES:
            raise SupervisorDecisionError("Terminal Workflow 상태에서는 복구할 수 없습니다.")
        if route.action not in {"RETRY", "SAFE_STOP", "TOOL_FALLBACK", "RUN_AGENT"}:
            raise SupervisorDecisionError("등록되지 않은 복구 결정입니다.")
        next_state = SupervisorWorkflowState(
            workflow_id=state.workflow_id,
            status=state.status,
            state_version=state.state_version + 1,
            retry_count=max(state.retry_count, route.retry_count),
            revision_count=max(state.revision_count, route.revision_count),
        )
        return SupervisorTransition(
            state=next_state,
            from_state=state.status,
            decision_id=str(uuid4()),
            event="recovery_decided",
            action=route.action,
            reason_code=f"RECOVERY_{route.action}",
            reason=route.reason,
            policy_version=policy_version,
            matched_rule_id=error_code,
            error_code=error_code,
            evidence_refs=evidence_refs,
        )

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
        from backend.app.fault_injection.injector import perturb

        context = perturb("context.validate", context)
        self._validate_context_secrets(context)
        if "user_request" in context:
            request = context["user_request"]
            if not isinstance(request, Mapping) or any(
                key not in request for key in self._BRIEF_KEYS
            ):
                raise ContextPolicyError("Agent에게 전달할 Event Brief가 올바르지 않습니다.")
            context["user_request"] = {key: request[key] for key in self._BRIEF_KEYS}

        return context

    def _validate_context_secrets(self, value: object) -> None:
        if isinstance(value, Mapping):
            if self._FORBIDDEN_KEYS.intersection(str(key).casefold() for key in value):
                raise ContextPolicyError("Agent Context에 금지된 중첩 필드가 있습니다.")
            for item in value.values():
                self._validate_context_secrets(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._validate_context_secrets(item)

    def build_planner_revision_context(
        self, context: Mapping[str, object], feedback: str
    ) -> dict[str, object]:
        """Add bounded review feedback without granting authority over Brief fields."""
        from backend.app.guards.input_guard import validate_text

        if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 1000:
            raise ContextPolicyError("Planner 수정 피드백 계약이 올바르지 않습니다.")
        validate_text(feedback)
        result = deepcopy(dict(context))
        self._validate_context_secrets(result)
        result["repair_feedback"] = {
            "reason": feedback,
            "modifiable_fields": ["summary", "action_rationales", "reward_rationales"],
            "fixed_brief": True,
        }
        return result

    def build_canonical_planner_context(self, state: Mapping[str, object]) -> dict[str, object]:
        """Give the Planner only user-facing inputs; keep workflow IDs in Supervisor state."""
        context = self.build_agent_context("canonical_event_planner_agent", state)
        brief = context.get("brief")
        if not isinstance(brief, Mapping):
            raise ContextPolicyError("Planner Brief가 올바르지 않습니다.")
        planner_brief = deepcopy(dict(brief))
        actions = planner_brief.get("actions")
        rewards = planner_brief.get("rewards")
        links = planner_brief.get("mission_reward_links")
        if (
            not isinstance(actions, list)
            or not isinstance(rewards, list)
            or not isinstance(links, list)
        ):
            raise ContextPolicyError("Planner Brief의 미션·보상 목록이 올바르지 않습니다.")
        action_indexes = {
            row.get("action_id"): index
            for index, row in enumerate(actions)
            if isinstance(row, Mapping) and isinstance(row.get("action_id"), str)
        }
        reward_indexes = {
            row.get("reward_id"): index
            for index, row in enumerate(rewards)
            if isinstance(row, Mapping) and isinstance(row.get("reward_id"), str)
        }
        for row in actions:
            if not isinstance(row, dict) or not isinstance(row.get("action_id"), str):
                raise ContextPolicyError("Planner action ID가 올바르지 않습니다.")
            row.pop("action_id")
        for row in rewards:
            if not isinstance(row, dict) or not isinstance(row.get("reward_id"), str):
                raise ContextPolicyError("Planner reward ID가 올바르지 않습니다.")
            row.pop("reward_id")
        planner_links = []
        for row in links:
            if not isinstance(row, Mapping):
                raise ContextPolicyError("Planner mission-reward 연결이 올바르지 않습니다.")
            action_index = action_indexes.get(row.get("action_id"))
            reward_index = reward_indexes.get(row.get("reward_id"))
            if action_index is None or reward_index is None:
                raise ContextPolicyError(
                    "Planner mission-reward ID 참조가 Brief와 일치하지 않습니다."
                )
            planner_links.append({"action_index": action_index, "reward_index": reward_index})
        planner_brief["mission_reward_links"] = planner_links
        context["brief"] = planner_brief
        context["input_contract_version"] = "canonical-planner-input.v2"
        return context

    @staticmethod
    def bind_canonical_planner_output(
        brief: Mapping[str, object], output: Mapping[str, object]
    ) -> dict[str, object]:
        """Attach authoritative Brief IDs to positional Planner rationales."""
        actions = brief.get("actions")
        rewards = brief.get("rewards")
        action_rationales = output.get("action_rationales")
        reward_rationales = output.get("reward_rationales")
        reward_proposals = output.get("reward_proposals", [])
        if not all(
            isinstance(value, list)
            for value in (actions, rewards, action_rationales, reward_rationales, reward_proposals)
        ):
            raise SupervisorDecisionError("Planner 출력 목록이 올바르지 않습니다.")
        if len(actions) != len(action_rationales) or len(rewards) != len(reward_rationales):
            raise SupervisorDecisionError("Planner 설명 개수가 Brief 항목 수와 일치하지 않습니다.")
        auto_rewards = brief.get("reward_selection") == "planner"
        if auto_rewards:
            if rewards or len(reward_proposals) != len(actions):
                raise SupervisorDecisionError(
                    "Planner 보상 제안 개수가 미션 수와 일치하지 않습니다."
                )
            if any(not isinstance(row, Mapping) for row in reward_proposals):
                raise SupervisorDecisionError("Planner 보상 제안 형식이 올바르지 않습니다.")
            proposal_indexes = [row.get("action_index") for row in reward_proposals]
            if any(type(index) is not int for index in proposal_indexes):
                raise SupervisorDecisionError("Planner 보상 제안의 미션 참조가 올바르지 않습니다.")
            if sorted(proposal_indexes) != list(range(len(actions))):
                raise SupervisorDecisionError("Planner 보상 제안의 미션 참조가 올바르지 않습니다.")
        elif reward_proposals:
            raise SupervisorDecisionError("명시된 보상 Brief에 새 보상 제안이 포함됐습니다.")
        bound_actions = []
        bound_rewards = []
        for source, rationale in zip(actions, action_rationales, strict=True):
            if not isinstance(source, Mapping) or not isinstance(rationale, Mapping):
                raise SupervisorDecisionError("Planner 미션 설명 형식이 올바르지 않습니다.")
            action_id = source.get("action_id")
            text = rationale.get("rationale")
            if not isinstance(action_id, str) or not isinstance(text, str):
                raise SupervisorDecisionError("Planner 미션 ID 또는 설명이 올바르지 않습니다.")
            bound_actions.append({"action_id": action_id, "rationale": text})
        for source, rationale in zip(rewards, reward_rationales, strict=True):
            if not isinstance(source, Mapping) or not isinstance(rationale, Mapping):
                raise SupervisorDecisionError("Planner 보상 설명 형식이 올바르지 않습니다.")
            reward_id = source.get("reward_id")
            text = rationale.get("rationale")
            if not isinstance(reward_id, str) or not isinstance(text, str):
                raise SupervisorDecisionError("Planner 보상 ID 또는 설명이 올바르지 않습니다.")
            bound_rewards.append({"reward_id": reward_id, "rationale": text})
        return {
            "summary": output.get("summary"),
            "action_rationales": bound_actions,
            "reward_rationales": bound_rewards,
            "reward_proposals": reward_proposals,
        }

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
        from backend.app.fault_injection.injector import observe_failure

        observe_failure(error_type)
        return decide_recovery(
            error_type,
            current_agent=current_agent,
            failed_tool=failed_tool,
            retry_count=retry_count,
            revision_count=revision_count,
        )
