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
        DayPlan(
            "모험 시작",
            "일반 사냥 20회",
            "HUNT_001",
            "일반 사냥",
            "monster_kill",
            20,
            "ITEM_001",
            "회복 포션",
            10,
            "기본 전투 방식에 익숙해지도록 구성",
        ),
        DayPlan(
            "사냥 적응",
            "일반 사냥 30회",
            "HUNT_001",
            "일반 사냥",
            "monster_kill",
            30,
            "ITEM_002",
            "성장 상자",
            1,
            "초기 전투 참여를 이어가도록 구성",
        ),
        DayPlan(
            "첫 번째 도전",
            "일반 던전 1회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            1,
            "ITEM_001",
            "회복 포션",
            10,
            "던전 콘텐츠를 처음 경험하도록 구성",
        ),
        DayPlan(
            "던전 성장",
            "일반 던전 2회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            2,
            "ITEM_002",
            "성장 상자",
            1,
            "반복 플레이로 성장 흐름을 익히도록 구성",
        ),
        DayPlan(
            "성장 가속",
            "일반 던전 3회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            3,
            "ITEM_003",
            "기념 배지",
            1,
            "중간 달성 보상으로 참여를 유지하도록 구성",
        ),
        DayPlan(
            "성장 마무리",
            "일반 던전 3회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            3,
            "ITEM_002",
            "성장 상자",
            1,
            "마지막 날을 준비하는 반복 미션",
        ),
        DayPlan(
            "7일 성장 완료",
            "일반 던전 3회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            3,
            "ITEM_004",
            "한정 코스튬",
            1,
            "입장 제한이 높은 레이드 대신 일반 던전으로 구성",
        ),
    ),
    "returning_user": (
        DayPlan(
            "다시 시작",
            "일반 사냥 20회",
            "HUNT_001",
            "일반 사냥",
            "monster_kill",
            20,
            "ITEM_001",
            "회복 포션",
            10,
            "전투 감각을 되찾도록 구성",
        ),
        DayPlan(
            "콘텐츠 복습",
            "일반 던전 1회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            1,
            "ITEM_002",
            "성장 상자",
            1,
            "기본 던전 흐름을 다시 경험하도록 구성",
        ),
        DayPlan(
            "적응 도전",
            "일반 던전 2회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            2,
            "ITEM_001",
            "회복 포션",
            10,
            "반복 플레이에 적응하도록 구성",
        ),
        DayPlan(
            "참여 확대",
            "일반 사냥 30회",
            "HUNT_001",
            "일반 사냥",
            "monster_kill",
            30,
            "ITEM_002",
            "성장 상자",
            1,
            "일일 플레이 참여를 늘리도록 구성",
        ),
        DayPlan(
            "복귀 완료",
            "일반 던전 3회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            3,
            "ITEM_003",
            "기념 배지",
            1,
            "복귀 적응을 마무리하도록 구성",
        ),
    ),
    "all_user": (
        DayPlan(
            "주말 시작",
            "일반 사냥 20회",
            "HUNT_001",
            "일반 사냥",
            "monster_kill",
            20,
            "ITEM_001",
            "회복 포션",
            10,
            "짧은 참여 미션으로 시작",
        ),
        DayPlan(
            "주말 도전",
            "일반 던전 2회 완료",
            "DUNGEON_001",
            "일반 던전",
            "dungeon_clear",
            2,
            "ITEM_002",
            "성장 상자",
            1,
            "주요 콘텐츠 참여를 유도",
        ),
        DayPlan(
            "주말 마무리",
            "일반 사냥 30회",
            "HUNT_001",
            "일반 사냥",
            "monster_kill",
            30,
            "ITEM_003",
            "기념 배지",
            1,
            "참여를 마무리하는 보상 제공",
        ),
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
    has_safe_stop = target == "all_user"
    autohealing_events = (
        [
            {
                "sequence": 1,
                "error_type": "mission_unreachable",
                "reason": "오류 주입 데모: 기획용 데이터 제공 후에도 Day 7 레이드 Lv.30을 선택해 신규 유저 예상 레벨 Lv.15로는 달성할 수 없습니다.",
                "recovery_action": "RUN_AGENT",
                "target": "event_planner_agent",
                "before": {
                    "day": 7,
                    "content_id": "RAID_001",
                    "content_name": "레이드",
                    "mission_name": "레이드 보스 1회 처치",
                    "required_level": 30,
                },
                "after": {
                    "day": 7,
                    "content_id": "DUNGEON_001",
                    "content_name": "일반 던전",
                    "mission_name": "일반 던전 3회 완료",
                    "required_level": 10,
                },
                "revalidation_status": "PASS",
            }
        ]
        if has_healing
        else []
    )
    if has_safe_stop:
        autohealing_events = [
            {
                "sequence": 1,
                "error_type": "mcp_timeout",
                "reason": "데모 시나리오: 초안 생성 전 기획용 MCP 데이터 조회가 반복해서 시간 초과됐습니다.",
                "recovery_action": "SAFE_STOP",
                "target": "mcp",
                "attempts": 3,
                "before": None,
                "after": None,
                "revalidation_status": "NOT_RUN",
            }
        ]
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
    result_version = 0 if has_safe_stop else 2 if has_healing else 1
    task = {
        "task_id": task_id,
        "workflow_status": "FAILED" if has_safe_stop else "WAITING_APPROVAL",
        "critic_status": None if has_safe_stop else "PASS",
        "approval_status": None if has_safe_stop else "PENDING",
        "result_version": result_version,
        "retry_count": 3 if has_safe_stop else 0,
        "revision_count": 1 if has_healing else 0,
        "current_result": None if has_safe_stop else result,
        "final_result": None,
        "rule_validation": None
        if has_safe_stop
        else {
            "is_valid": True,
            "error_type": None,
            "reason": (
                "데모 시나리오: Day 7을 일반 던전으로 바꾼 뒤 필요 레벨 Lv.10이 "
                "신규 유저 예상 레벨 Lv.15 이하라 재검증 PASS로 표시합니다. "
                "실제 게임 규칙 검증은 수행하지 않았습니다."
                if has_healing
                else "화면 체험용 검증 결과입니다. 실제 게임 규칙 검증은 수행하지 않았습니다."
            ),
            "evidence": (
                {
                    "mode": "demo_preview",
                    "backend_checked": False,
                    "day": 7,
                    "expected_level": 15,
                    "required_level": 10,
                    "content_id": "DUNGEON_001",
                }
                if has_healing
                else {"mode": "demo_preview", "backend_checked": False}
            ),
            "game_data_version": None,
            "data_source": "demo_fixture",
        },
        "autohealing_events": autohealing_events,
        "error": {
            "error_type": "mcp_timeout",
            "message": "기획용 MCP 데이터 조회가 3회 재시도 후에도 시간 초과되어 초안 생성 전 안전 종료했습니다.",
        }
        if has_safe_stop
        else None,
        "_demo_preview": True,
    }

    events = []
    if has_safe_stop:
        events.extend(
            [
                (
                    "mcp",
                    "get_event_planning_context",
                    "ERROR",
                    "기획용 MCP 데이터 조회 시간 초과 · 첫 호출 실패",
                    "planning_context",
                ),
                ("supervisor", "RETRY", "SUCCESS", "기술 재시도 1회 결정", None),
                (
                    "mcp",
                    "get_event_planning_context",
                    "ERROR",
                    "기획용 MCP 데이터 조회 시간 초과 · 재시도 1회 실패",
                    "planning_context",
                ),
                ("supervisor", "RETRY", "SUCCESS", "기술 재시도 2회 결정", None),
                (
                    "mcp",
                    "get_event_planning_context",
                    "ERROR",
                    "기획용 MCP 데이터 조회 시간 초과 · 재시도 2회 실패",
                    "planning_context",
                ),
                ("supervisor", "RETRY", "SUCCESS", "기술 재시도 3회 결정", None),
                (
                    "mcp",
                    "get_event_planning_context",
                    "ERROR",
                    "기획용 MCP 데이터 조회 시간 초과 · 재시도 3회 실패",
                    "planning_context",
                ),
                (
                    "supervisor",
                    "SAFE_STOP",
                    "FAILED",
                    "재시도 한도 초과 · 초안 생성 전 안전 종료",
                    None,
                ),
            ]
        )
    else:
        events.extend(
            [
                (
                    "mcp",
                    "get_event_planning_context",
                    "SUCCESS",
                    "시연용 대상 유저·콘텐츠·아이템 기획 데이터 조회",
                    "planning_context",
                ),
                (
                    "supervisor",
                    "RUN_AGENT",
                    "SUCCESS",
                    "기획 데이터를 확인하고 Event Planner에 초안 생성을 지시",
                    "routing",
                    "event_planner_agent",
                ),
                (
                    "event_planner_agent",
                    "create_plan",
                    "SUCCESS",
                    "기획 데이터를 전달받아 예시 초안 생성"
                    + (" · 오류 주입으로 Day 7 레이드 포함" if has_healing else ""),
                    None,
                ),
                (
                    "supervisor",
                    "RUN_AGENT",
                    "SUCCESS",
                    "초안의 게임 규칙 검증을 Balance & Rule에 지시",
                    "routing",
                    "balance_rule_agent",
                ),
                ("mcp", "get_game_rules", "SUCCESS", "초안의 게임 규칙 조회", "validation"),
                ("mcp", "get_item_info", "SUCCESS", "초안의 보상 아이템 조회", "validation"),
            ]
        )
        if has_healing:
            events.extend(
                [
                    (
                        "balance_rule_agent",
                        "validate_plan",
                        "ERROR",
                        "오류 주입 데모: Day 7 레이드 Lv.30이 신규 유저 예상 Lv.15를 초과함",
                        "validation",
                    ),
                    ("supervisor", "RUN_AGENT", "SUCCESS", "기획 오류 수정 경로 결정", None),
                    (
                        "event_planner_agent",
                        "regenerate_plan",
                        "SUCCESS",
                        "예시 기획안을 일반 던전 미션으로 수정",
                        None,
                    ),
                    (
                        "supervisor",
                        "RUN_AGENT",
                        "SUCCESS",
                        "수정안의 재검증을 Balance & Rule에 지시",
                        "routing",
                        "balance_rule_agent",
                    ),
                    (
                        "mcp",
                        "get_game_rules",
                        "SUCCESS",
                        "수정안의 시연용 게임 규칙 재조회",
                        "revalidation",
                    ),
                    (
                        "mcp",
                        "get_item_info",
                        "SUCCESS",
                        "수정안의 시연용 보상 아이템 재조회",
                        "revalidation",
                    ),
                ]
            )
        events.extend(
            [
                (
                    "balance_rule_agent",
                    "validate_plan",
                    "SUCCESS",
                    "수정한 Day 7 일반 던전 Lv.10 ≤ 신규 유저 예상 Lv.15 · 시연용 재검증 PASS"
                    if has_healing
                    else "시연용 규칙 검증 결과",
                    "revalidation" if has_healing else "validation",
                ),
                (
                    "supervisor",
                    "RUN_AGENT",
                    "SUCCESS",
                    "게임 규칙 통과 후 Critic에 품질 검토를 지시",
                    "routing",
                    "critic_agent",
                ),
                ("critic_agent", "final_review", "SUCCESS", "시연용 Critic PASS", None),
                (
                    "supervisor",
                    "WAIT_APPROVAL",
                    "SUCCESS",
                    "Critic PASS를 확인하고 운영자 승인 대기로 전환",
                    "routing",
                    None,
                ),
            ]
        )
    start = datetime.now().astimezone().replace(microsecond=0) - timedelta(seconds=len(events) * 5)
    revision_sequence = next(
        (
            sequence
            for sequence, event in enumerate(events, start=1)
            if event[0] == "event_planner_agent" and event[1] == "regenerate_plan"
        ),
        None,
    )
    traces = []
    for sequence, event in enumerate(events, start=1):
        actor, action, status, reason, phase, *route = event
        traces.append(
            {
                "sequence": sequence,
                "time": (start + timedelta(seconds=sequence * 5)).isoformat(),
                "actor": actor,
                "action": action,
                "status": status,
                "reason": reason,
                "error_type": ("mcp_timeout" if has_safe_stop else "mission_unreachable")
                if status in {"ERROR", "FAILED"}
                else None,
                "retry_count": min(sequence // 2, 3) if has_safe_stop else 0,
                "revision_count": 1 if revision_sequence and sequence >= revision_sequence else 0,
                "result_version": result_version if actor == "critic_agent" else 0,
                "data_source": "demo_fixture",
                "phase": phase,
                "next_agent": route[0] if route else None,
            }
        )
    return task, {"task_id": task_id, "traces": traces}
