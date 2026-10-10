"""Read and validate approved source Tasks from the existing planner system."""

import hashlib
import json
from typing import Any

from backend.app.services.task_service import ServiceError, get_task


class EventSourceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _approved_task(task_id: str) -> Any:
    try:
        task = get_task(task_id)
    except ServiceError as exc:
        raise EventSourceError(exc.code, exc.message, exc.status_code) from exc
    if task.workflow_status.value != "COMPLETED":
        raise EventSourceError(
            "source_not_completed", "완료된 기획 Workflow만 적용할 수 있습니다.", 409
        )
    if task.approval_status is None or task.approval_status.value != "APPROVED":
        raise EventSourceError(
            "source_not_approved", "운영자 승인 완료 Task만 적용할 수 있습니다.", 409
        )
    if task.critic_status is None or task.critic_status.value != "PASS":
        raise EventSourceError(
            "source_critic_failed", "AI 검수를 통과한 Task만 적용할 수 있습니다.", 409
        )
    if task.rule_validation is None or not task.rule_validation.is_valid:
        raise EventSourceError(
            "source_validation_failed", "규칙 검증을 통과한 Task만 적용할 수 있습니다.", 409
        )
    if task.final_result is None:
        raise EventSourceError("source_result_missing", "승인된 기획 결과를 찾을 수 없습니다.", 409)
    if task.final_result.game_source != "opong":
        raise EventSourceError(
            "unsupported_game_source",
            "현재 가상 게임은 자체 Opong Heroes 이벤트만 지원합니다.",
            422,
        )
    return task


def preview_approved_event(task_id: str) -> dict[str, Any]:
    """Return minimal source metadata so the local UI can submit the latest version."""
    task = _approved_task(task_id)
    result = task.final_result
    return {
        "task_id": task.task_id,
        "result_version": task.result_version,
        "event_name": result.event_name,
        "duration": result.duration,
        "mission_count": len(result.missions),
        "reward_count": len(result.rewards),
        "target_user": result.target_user.value,
        "game_source": result.game_source,
        "game_data_version": task.rule_validation.game_data_version,
    }


def load_approved_event(task_id: str, result_version: int) -> dict[str, Any]:
    task = _approved_task(task_id)
    if task.result_version != result_version:
        raise EventSourceError(
            "source_version_conflict", "요청한 결과 버전이 최신 Task와 다릅니다.", 409
        )

    result = task.final_result.model_dump(mode="json")
    source = {
        "task_id": task.task_id,
        "result_version": task.result_version,
        "approval_status": task.approval_status.value,
        "workflow_status": task.workflow_status.value,
        "critic_status": task.critic_status.value,
        "validation": task.rule_validation.model_dump(mode="json"),
        "result": result,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"source": source, "digest": hashlib.sha256(encoded).hexdigest()}
