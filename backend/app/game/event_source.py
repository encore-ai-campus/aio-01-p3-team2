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


def _approved_canonical_event(task_id: str, result_version: int) -> dict[str, Any]:
    """Adapt an immutable, approved canonical Plan into the existing Runtime event contract."""
    from mcp_server.tools._opong_data import load_opong_snapshot

    from backend.app.services.canonical_planning import approved_plan_for_task

    approved = approved_plan_for_task(task_id, result_version)
    if approved is None:
        raise EventSourceError(
            "source_not_approved", "운영자 승인 완료 기획안만 적용할 수 있습니다.", 409
        )
    record = approved["record"]
    plan = record["plan"]
    decision = approved["decision"]
    snapshot_id = plan["game_context"]["game_data_snapshot_id"]
    snapshot = load_opong_snapshot(snapshot_id)
    catalog = snapshot["catalog"]
    audience = plan["audience"].get("segments", "new_user").split(",")
    target_user = audience[0] if len(audience) == 1 else "all_user"
    duration = plan["schedule"].get("duration_days")
    if duration is None:
        from datetime import datetime

        start = datetime.fromisoformat(plan["schedule"]["start_at"])
        end = datetime.fromisoformat(plan["schedule"]["end_at"])
        duration = max(1, (end - start).days)
    missions = []
    mission_days: dict[str, int] = {}
    for day, mission in enumerate(plan["missions"], start=1):
        scope_ids = (mission.get("content_scope") or {}).get("content_ids") or []
        content_id = scope_ids[0] if scope_ids else None
        content_name = "게임 규칙에서 확인된 행동"
        if content_id:
            for collection in (catalog["stages"], catalog["dungeons"]):
                match = next((item for item in collection if item["id"] == content_id), None)
                if match:
                    content_name = match["name"]
                    break
            for template in catalog["special_dungeon_templates"]:
                match = next(
                    (item for item in template["floors"] if item["id"] == content_id), None
                )
                if match:
                    content_name = match["name"]
                    break
        mission_days[mission["mission_id"]] = day
        missions.append(
            {
                "day": day,
                "content_id": content_id,
                "content_name": content_name,
                "mission_type": mission["action_type"],
                "target_count": mission["goal"]["target"],
                "mission_name": f"{content_name} {mission['goal']['target']}회 완료",
                "planning_reason": "canonical plan의 명시된 행동·목표를 Runtime 계약으로 변환했습니다.",
            }
        )
    rewards = [
        {
            "day": mission_days[reward["mission_id"]],
            "item_id": reward["item_id"],
            "item_name": catalog["items"][reward["item_id"]]["name"],
            "quantity": reward["quantity"],
        }
        for reward in plan["rewards"]
    ]
    result = {
        "event_name": plan["objective"][:100],
        "target_user": target_user,
        "duration": min(7, int(duration)),
        "objective": plan["objective"],
        "game_source": "opong",
        "source_game_data_digest": snapshot["snapshot_digest"],
        "event_concept": plan["event_type"],
        "operation_summary": "검증된 canonical plan을 운영자가 승인했습니다.",
        "operation_review_notes": {
            "mission_operation": "; ".join(item["mission_name"] for item in missions),
            "reward_design": "; ".join(
                f"{item['item_name']} {item['quantity']}개" for item in rewards
            )
            or "보상 없음",
            "critic_review": "서버의 형식·Capability 검증을 통과했습니다.",
            "source": "live",
        },
        "missions": missions,
        "rewards": rewards,
    }
    source = {
        "task_id": task_id,
        "result_version": result_version,
        "approval_status": decision["approval_status"],
        "workflow_status": "COMPLETED",
        "critic_status": "PASS",
        "validation": {
            "is_valid": True,
            "game_data_version": snapshot_id,
            "snapshot_digest": snapshot["snapshot_digest"],
            "data_source": snapshot["source"],
        },
        "result": result,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"source": source, "digest": hashlib.sha256(encoded).hexdigest()}


def preview_approved_event(task_id: str) -> dict[str, Any]:
    """Return minimal source metadata so the local UI can submit the latest version."""
    try:
        task = _approved_task(task_id)
    except EventSourceError as exc:
        if exc.code != "TASK_NOT_FOUND":
            raise
        from backend.app.services.canonical_planning import approved_plan_for_task

        approved = approved_plan_for_task(task_id, 1)
        if approved is None:
            raise
        plan = approved["record"]["plan"]
        return {
            "task_id": task_id,
            "result_version": 1,
            "event_name": plan["objective"][:100],
            "duration": plan["schedule"].get("duration_days"),
            "mission_count": len(plan["missions"]),
            "reward_count": len(plan["rewards"]),
            "target_user": plan["audience"].get("segments"),
            "game_source": "opong",
            "game_data_version": plan["game_context"]["game_data_snapshot_id"],
        }
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
    try:
        task = _approved_task(task_id)
    except EventSourceError as exc:
        if exc.code != "TASK_NOT_FOUND":
            raise
        return _approved_canonical_event(task_id, result_version)
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
