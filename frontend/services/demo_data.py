"""Clearly labeled, local-only sample results for frontend walkthroughs."""

from datetime import datetime, timedelta
from typing import Any, NamedTuple
from uuid import uuid4


class DayPlan(NamedTuple):
    title: str
    mission: str
    content_id: str
    content_name: str
    mission_type: str
    count: int
    item_id: str
    item_name: str
    quantity: int
    reason: str


DAY_PLANS = {
    "new_user": (
        DayPlan("모험 시작", "일반 사냥 20회", "HUNT_001", "일반 사냥", "monster_kill", 20, "ITEM_001", "회복 포션", 10, "기본 전투 방식에 익숙해지도록 구성"),
        DayPlan("사냥 적응", "일반 사냥 30회", "HUNT_001", "일반 사냥", "monster_kill", 30, "ITEM_002", "성장 상자", 1, "초기 전투 참여를 이어가도록 구성"),
        DayPlan("첫 번째 도전", "일반 던전 1회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 1, "ITEM_001", "회복 포션", 10, "던전 콘텐츠를 처음 경험하도록 구성"),
        DayPlan("던전 성장", "일반 던전 2회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 2, "ITEM_002", "성장 상자", 1, "반복 플레이로 성장 흐름을 익히도록 구성"),
        DayPlan("성장 가속", "일반 던전 3회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 3, "ITEM_003", "기념 배지", 1, "중간 달성 보상으로 참여를 유지하도록 구성"),
        DayPlan("성장 마무리", "일반 던전 3회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 3, "ITEM_002", "성장 상자", 1, "마지막 날을 준비하는 반복 미션"),
        DayPlan("7일 성장 완료", "일반 던전 3회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 3, "ITEM_004", "한정 코스튬", 1, "입장 제한이 높은 레이드 대신 일반 던전으로 구성"),
    ),
    "returning_user": (
        DayPlan("다시 시작", "일반 사냥 20회", "HUNT_001", "일반 사냥", "monster_kill", 20, "ITEM_001", "회복 포션", 10, "전투 감각을 되찾도록 구성"),
        DayPlan("콘텐츠 복습", "일반 던전 1회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 1, "ITEM_002", "성장 상자", 1, "기본 던전 흐름을 다시 경험하도록 구성"),
        DayPlan("적응 도전", "일반 던전 2회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 2, "ITEM_001", "회복 포션", 10, "반복 플레이에 적응하도록 구성"),
        DayPlan("참여 확대", "일반 사냥 30회", "HUNT_001", "일반 사냥", "monster_kill", 30, "ITEM_002", "성장 상자", 1, "일일 플레이 참여를 늘리도록 구성"),
        DayPlan("복귀 완료", "일반 던전 3회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 3, "ITEM_003", "기념 배지", 1, "복귀 적응을 마무리하도록 구성"),
    ),
    "all_user": (
        DayPlan("주말 시작", "일반 사냥 20회", "HUNT_001", "일반 사냥", "monster_kill", 20, "ITEM_001", "회복 포션", 10, "짧은 참여 미션으로 시작"),
        DayPlan("주말 도전", "일반 던전 2회 완료", "DUNGEON_001", "일반 던전", "dungeon_clear", 2, "ITEM_002", "성장 상자", 1, "주요 콘텐츠 참여를 유도"),
        DayPlan("주말 마무리", "일반 사냥 30회", "HUNT_001", "일반 사냥", "monster_kill", 30, "ITEM_003", "기념 배지", 1, "참여를 마무리하는 보상 제공"),
    ),
}


def build_demo_result(brief: dict[str, str | int]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a sample Task and Trace without contacting Backend or MCP."""
    target = str(brief["target_user"])
    days = DAY_PLANS[target]
    duration = int(brief["duration"])
    if duration != len(days):
        raise ValueError("데모는 제공된 예시 기간만 지원합니다.")

    missions = []
    rewards = []
    for day, plan in enumerate(days, start=1):
        missions.append(
            {
                "day": day,
                "day_title": plan.title,
                "content_id": plan.content_id,
                "content_name": plan.content_name,
                "mission_type": plan.mission_type,
                "mission_name": plan.mission,
                "target_count": plan.count,
                "planning_reason": plan.reason,
            }
        )
        rewards.append(
            {
                "day": day,
                "item_id": plan.item_id,
                "item_name": plan.item_name,
                "quantity": plan.quantity,
            }
        )

    has_healing = target == "new_user"
    autohealing_events = (
        [
            {
                "sequence": 1,
                "error_type": "mission_unreachable",
                "reason": "데모 시나리오: 신규 유저의 예상 레벨보다 레이드 입장 레벨이 높습니다.",
                "recovery_action": "RUN_AGENT",
                "target": "event_planner_agent",
                "before": {"day": 7, "content_id": "RAID_001", "mission_name": "레이드 보스 1회 처치", "required_level": 30},
                "after": {"day": 7, "content_id": "DUNGEON_001", "mission_name": "일반 던전 3회 완료", "required_level": 10},
                "revalidation_status": "PASS",
            }
        ]
        if has_healing
        else []
    )
    result = {
        "event_name": brief["event_name"],
        "target_user": target,
        "duration": duration,
        "objective": brief["objective"],
        "event_concept": f"{duration}일 동안 함께하는 영웅 미션",
        "operation_summary": "매일 예시 미션을 완료하고 보상을 확인합니다. 실제 게임 데이터와 연결되지 않은 데모입니다.",
        "missions": missions,
        "rewards": rewards,
    }

    task_id = f"DEMO-{uuid4().hex[:8].upper()}"
    result_version = 2 if has_healing else 1
    task = {
        "task_id": task_id,
        "workflow_status": "WAITING_APPROVAL",
        "critic_status": "PASS",
        "approval_status": "PENDING",
        "result_version": result_version,
        "retry_count": 0,
        "revision_count": 1 if has_healing else 0,
        "current_result": result,
        "final_result": None,
        "rule_validation": {
            "is_valid": True,
            "error_type": None,
            "reason": "화면 체험용 검증 결과입니다. 실제 게임 규칙 검증은 수행하지 않았습니다.",
            "evidence": {"mode": "demo_preview", "backend_checked": False},
            "game_data_version": None,
            "data_source": "demo_fixture",
        },
        "autohealing_events": autohealing_events,
        "error": None,
        "_demo_preview": True,
    }

    start = datetime.now().astimezone().replace(microsecond=0) - timedelta(seconds=30)
    events = [("event_planner_agent", "create_plan", "SUCCESS", "예시 기획안 생성")]
    if has_healing:
        events.extend(
            [
                ("balance_rule_agent", "validate_plan", "ERROR", "데모 오류: mission_unreachable"),
                ("supervisor", "RUN_AGENT", "SUCCESS", "데모 AutoHealing 재실행"),
                ("event_planner_agent", "regenerate_plan", "SUCCESS", "예시 기획안 수정"),
            ]
        )
    events.extend(
        [
            ("balance_rule_agent", "validate_plan", "SUCCESS", "시연용 규칙 검증 결과"),
            ("critic_agent", "final_review", "SUCCESS", "시연용 Critic PASS"),
        ]
    )
    traces = [
        {
            "sequence": sequence,
            "time": (start + timedelta(seconds=sequence * 5)).isoformat(),
            "actor": actor,
            "action": action,
            "status": status,
            "reason": reason,
            "error_type": "mission_unreachable" if status == "ERROR" else None,
            "retry_count": 0,
            "revision_count": 1 if has_healing and sequence >= 3 else 0,
            "result_version": result_version if actor == "critic_agent" else 0,
            "data_source": "demo_fixture",
        }
        for sequence, (actor, action, status, reason) in enumerate(events, start=1)
    ]
    return task, {"task_id": task_id, "traces": traces}
