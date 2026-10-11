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
    input_contract_version: str
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
        input_contract_version: str = "workflow-handoff.v1",
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
        if not objective or not expected_output or not input_contract_version:
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
            input_contract_version=input_contract_version,
            constraints=tuple(constraints),
            expected_output=expected_output,
            attempt=attempt,
            revision_count=revision_count,
            retry_count=retry_count,
            error_context=deepcopy(dict(error_context or {})),
            state_ref=state_ref,
        )

    def validate_receiver(
        self,
        *,
        expected_contract_version: str,
        required_inputs: Sequence[str],
        expected_game_data_version: str | None = None,
        game_data_versions: Sequence[object] = (),
        expected_task_id: str | None = None,
        expected_receiver: str | None = None,
        expected_sender: str | None = None,
        expected_state_ref: str | None = None,
    ) -> None:
        """수신 Agent를 호출하기 전에 입력 계약과 필수 필드를 검사한다."""
        if self.handoff_status is not HandoffStatus.REQUESTED:
            raise ValueError("요청 상태의 Handoff만 수신 검사할 수 있습니다.")
        if expected_task_id is not None and self.task_id != expected_task_id:
            raise ValueError("Handoff 작업이 현재 실행과 다릅니다.")
        if expected_receiver is not None and self.to_agent != expected_receiver:
            raise ValueError("Handoff 수신 Agent가 현재 실행과 다릅니다.")
        if expected_sender is not None and self.from_agent != expected_sender:
            raise ValueError("Handoff 발신 Agent가 현재 실행과 다릅니다.")
        if expected_state_ref is not None and self.state_ref != expected_state_ref:
            raise ValueError("Handoff 상태 참조가 현재 Workflow와 다릅니다.")
        if self.input_contract_version != expected_contract_version:
            raise ValueError("Handoff 입력 계약 버전이 수신 Agent 계약과 일치하지 않습니다.")
        if not all(isinstance(key, str) and key for key in required_inputs):
            raise ValueError("Handoff 필수 입력 계약이 올바르지 않습니다.")
        if any(key not in self.inputs or self.inputs[key] is None for key in required_inputs):
            raise ValueError("Handoff 필수 입력이 누락되었습니다.")
        if expected_game_data_version is not None and (
            not expected_game_data_version
            or not game_data_versions
            or any(version != expected_game_data_version for version in game_data_versions)
        ):
            raise ValueError("Handoff 게임 데이터 버전이 현재 Workflow와 일치하지 않습니다.")

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
