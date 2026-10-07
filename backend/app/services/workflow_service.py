"""Backend A service boundary with a deterministic mock Workflow Engine."""

from datetime import datetime

from backend.app.schemas.event import (
    EventBrief,
    EventResult,
    MissionResult,
    RewardResult,
    RuleValidation,
)
from backend.app.schemas.workflow_state import (
    ApprovalStatus,
    CriticStatus,
    WorkflowResult,
    WorkflowStatus,
)

CONTENT = {
    1: ("HUNT_001", "일반 사냥", "monster_kill"),
    2: ("HUNT_001", "일반 사냥", "monster_kill"),
    3: ("DUNGEON_001", "일반 던전", "dungeon_clear"),
}
REWARDS = {
    1: ("ITEM_001", "회복 포션", 10),
    2: ("ITEM_002", "성장 상자", 1),
    3: ("ITEM_003", "기념 배지", 1),
}


class MockWorkflowEngine:
    """Contract-compatible placeholder; it does not call an LLM or MCP."""

    def run(self, task_id: str, brief: EventBrief, fault_type: str | None = None) -> WorkflowResult:
        del task_id, fault_type
        return self._result(brief)

    def revise(self, task_id: str, brief: EventBrief, approval_comment: str) -> WorkflowResult:
        del task_id
        result = self._result(brief)
        if result.current_result:
            result.current_result.operation_summary = f"운영자 수정 의견 반영: {approval_comment}"
        return result

    def _result(self, brief: EventBrief) -> WorkflowResult:
        missions: list[MissionResult] = []
        rewards: list[RewardResult] = []
        for day in range(1, brief.duration + 1):
            content_id, content_name, mission_type = CONTENT.get(day, CONTENT[3])
            item_id, item_name, quantity = REWARDS.get(day, REWARDS[2])
            target_count = 20 if mission_type == "monster_kill" else min(day, 3)
            missions.append(
                MissionResult(
                    day=day,
                    day_title=f"{day}일차 성장",
                    content_id=content_id,
                    content_name=content_name,
                    mission_type=mission_type,
                    mission_name=f"{content_name} {target_count}회 완료",
                    target_count=target_count,
                    planning_reason="이벤트 목표에 맞춘 MockEngine 기획안",
                )
            )
            rewards.append(
                RewardResult(
                    day=day,
                    item_id=item_id,
                    item_name=item_name,
                    quantity=quantity,
                )
            )
        event = EventResult(
            **brief.model_dump(),
            event_concept=f"{brief.duration}일 성장 지원 이벤트",
            operation_summary="MockEngine 결과이며 실제 AI·MCP 검증 전 단계입니다.",
            missions=missions,
            rewards=rewards,
        )
        validation = RuleValidation(
            is_valid=True,
            error_type=None,
            reason="MockEngine 계약 검증을 통과했습니다. 실제 게임 규칙 검증은 수행하지 않았습니다.",
            evidence={"mode": "mock_engine", "backend_checked": False},
            game_data_version=None,
            data_source="mock_engine",
        )
        return WorkflowResult(
            workflow_status=WorkflowStatus.WAITING_APPROVAL,
            critic_status=CriticStatus.PASS,
            approval_status=ApprovalStatus.PENDING,
            current_result=event,
            rule_validation=validation,
        )


workflow_engine = MockWorkflowEngine()


def now() -> datetime:
    return datetime.now().astimezone()
