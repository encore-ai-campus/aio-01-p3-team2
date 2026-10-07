"""Agent 간 작업 전달과 소유권 이전 조건을 관리한다."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import StrEnum


class HandoffStatus(StrEnum):
    """작업 전달의 요청, 수락, 거절 상태를 나타낸다."""

    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Handoff:
    """Agent 간 전달 정보와 현재 작업 소유자를 보관한다."""

    task_id: str
    from_agent: str
    to_agent: str
    owner_agent: str
    handoff_status: HandoffStatus
    objective: str
    inputs: dict[str, object]
    constraints: tuple[str, ...]
    expected_output: str
    attempt: int
    revision_count: int
    retry_count: int
    error_context: dict[str, object]
    state_ref: str | None

    @classmethod
    def request(
        cls,
        *,
        task_id: str,
        from_agent: str,
        to_agent: str,
        owner_agent: str,
        objective: str,
        inputs: Mapping[str, object],
        constraints: Sequence[str],
        expected_output: str,
        attempt: int,
        revision_count: int,
        retry_count: int,
        error_context: Mapping[str, object] | None = None,
        state_ref: str | None = None,
    ) -> "Handoff":
        """요청 시점에는 현재 작업 소유자를 유지한 전달 기록을 만든다."""
        if not task_id or not from_agent or not to_agent or not owner_agent:
            raise ValueError("Handoff의 작업 ID와 Agent 이름이 필요합니다.")
        if not objective or not expected_output:
            raise ValueError("Handoff의 목적과 예상 결과가 필요합니다.")
        if any(
            type(value) is not int or value < 0 for value in (attempt, revision_count, retry_count)
        ):
            raise ValueError("Handoff 횟수는 0 이상의 정수여야 합니다.")
        if not all(isinstance(value, str) for value in constraints):
            raise ValueError("Handoff 제약은 문자열이어야 합니다.")

        return cls(
            task_id=task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            owner_agent=owner_agent,
            handoff_status=HandoffStatus.REQUESTED,
            objective=objective,
            inputs=deepcopy(dict(inputs)),
            constraints=tuple(constraints),
            expected_output=expected_output,
            attempt=attempt,
            revision_count=revision_count,
            retry_count=retry_count,
            error_context=deepcopy(dict(error_context or {})),
            state_ref=state_ref,
        )

    def accept(self, *, required_inputs_present: bool, output_validated: bool) -> "Handoff":
        """필수 입력과 출력 검증을 통과했을 때만 소유권을 이전한다."""
        if self.handoff_status is not HandoffStatus.REQUESTED:
            raise ValueError("요청 상태의 Handoff만 수락할 수 있습니다.")
        if not required_inputs_present or not output_validated:
            raise ValueError("입력과 출력 검증을 통과해야 Handoff를 수락할 수 있습니다.")
        return replace(
            self,
            owner_agent=self.to_agent,
            handoff_status=HandoffStatus.ACCEPTED,
        )

    def reject(self, reason: str) -> "Handoff":
        """전달을 거절하고 기존 작업 소유자와 거절 사유를 보존한다."""
        if self.handoff_status is not HandoffStatus.REQUESTED:
            raise ValueError("요청 상태의 Handoff만 거절할 수 있습니다.")
        if not reason.strip():
            raise ValueError("Handoff 거절 사유가 필요합니다.")
        error_context = deepcopy(self.error_context)
        error_context["handoff_rejection_reason"] = reason
        return replace(
            self,
            handoff_status=HandoffStatus.REJECTED,
            error_context=error_context,
        )
