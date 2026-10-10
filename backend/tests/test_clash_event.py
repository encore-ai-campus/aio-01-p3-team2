"""Clash sample planning uses one snapshot and only demo-playable rewards."""

import asyncio
from uuid import uuid4

import pytest
from backend.app.agents.supervisor import Supervisor
from backend.app.schemas.event import EventBrief
from backend.app.services import task_service
from backend.app.services.clash_event import ClashEventWorkflow, validate_plan, validate_sample
from backend.app.services.llm_service import LLMService
from backend.app.services.workflow_service import BackendBWorkflowAdapter


def sample():
    return {
        "source": "clash_of_clans",
        "game_data_version": "clash-sample-test123",
        "sample": {
            "sampled_at": "2026-10-09T00:00:00+00:00",
            "selection": "global_player_ranking_top_20_and_clan_ranking_top_3",
            "player_count": 20,
            "clan_count": 3,
            "median_trophies": 4200,
            "median_town_hall": 14,
            "median_clan_members": 35,
        },
    }


def brief():
    return EventBrief(
        event_name="공개 표본 원정",
        target_user="all_user",
        duration=2,
        objective="마을 원정 참여",
        game_source="clash_of_clans",
    )


def plan():
    return {
        "target_user": "all_user",
        "duration": 2,
        "missions": [
            {"day": 1, "content_id": "COC_RAID_WIN", "mission_type": "raid_win", "target_count": 2},
            {
                "day": 2,
                "content_id": "COC_TROPHY_GAIN",
                "mission_type": "trophy_gain",
                "target_count": 6,
            },
        ],
        "rewards": [
            {"day": 1, "item_id": "DEMO_GOLD", "quantity": 40},
            {"day": 2, "item_id": "DEMO_POTION", "quantity": 1},
        ],
    }


def test_clash_workflow_uses_one_mcp_sample_and_returns_approvable_demo_plan():
    calls = []
    traces = []
    agent_calls = []

    async def mcp_transport(tool, arguments):
        calls.append((tool, arguments))
        return sample()

    async def llm_transport(agent, context):
        agent_calls.append(agent)
        if agent == "event_planner_agent":
            assert context["planning_context"]["execution_mode"] == "demo_game_only"
            return plan()
        if agent == "balance_rule_agent":
            assert context["review_scope"]["real_account_delivery_required"] is False
            assert context["deterministic_validation"]["is_valid"] is True
            return {"is_valid": True, "reason": "표본 상한과 데모 규칙에 맞습니다."}
        if agent == "critic_agent":
            return {
                "status": "PASS",
                "error_type": None,
                "reason": "실행 가능한 데모 기획안",
                "review_note": "표본 한계를 표시함",
            }
        if agent == "supervisor":
            return {"approved": True, "reason": "검증과 Critic 검토를 통과했습니다."}
        raise AssertionError(agent)

    async def trace_sink(event):
        traces.append(event)

    llm = LLMService(llm_transport, 5)
    workflow = ClashEventWorkflow(
        mcp_transport, llm, Supervisor({"context_access": {}}, llm), trace_sink
    )
    run = asyncio.run(workflow.run("TASK-123456789ABC", brief()))

    assert calls == [("get_clash_event_sample", {})]
    assert run.result.workflow_status == "WAITING_APPROVAL"
    assert run.result.rule_validation.game_data_version == sample()["game_data_version"]
    assert run.result.current_result.rewards[0].item_name == "데모 금화"
    assert run.snapshot == sample()
    assert any(trace["action"] == "get_clash_event_sample" for trace in traces)
    assert agent_calls == [
        "event_planner_agent",
        "balance_rule_agent",
        "critic_agent",
    ]
    assert [trace["actor"] for trace in traces] == [
        "mcp",
        "event_planner_agent",
        "balance_rule_agent",
        "critic_agent",
        "supervisor",
    ]


def test_clash_validation_rejects_unplayable_goal_and_real_reward():
    proposed = plan()
    proposed["missions"][0]["target_count"] = 30
    with pytest.raises(ValueError, match="달성 범위"):
        validate_plan(proposed, brief(), validate_sample(sample()))

    proposed = plan()
    proposed["rewards"][0]["item_id"] = "COC_GEMS"
    with pytest.raises(ValueError, match="보상"):
        validate_plan(proposed, brief(), validate_sample(sample()))


def test_clash_real_validation_failure_records_supervised_replanning_and_revalidation():
    traces = []
    planner_calls = []
    mcp_calls = []

    async def mcp_transport(tool, arguments):
        mcp_calls.append((tool, arguments))
        return sample()

    async def llm_transport(agent, context):
        if agent == "event_planner_agent":
            planner_calls.append(context)
            candidate = plan()
            if len(planner_calls) == 1:
                candidate["missions"][0]["target_count"] = 30
            return candidate
        if agent == "balance_rule_agent":
            assert context["deterministic_validation"]["is_valid"] is True
            return {"is_valid": True, "reason": "검증된 기획안"}
        if agent == "critic_agent":
            return {"status": "PASS", "error_type": None, "reason": "검수 통과"}
        if agent == "supervisor":
            return {"approved": True, "reason": "승인 대기 가능"}
        raise AssertionError(agent)

    async def trace_sink(event):
        traces.append(event)

    llm = LLMService(llm_transport, 5)
    workflow = ClashEventWorkflow(
        mcp_transport, llm, Supervisor({"context_access": {}}, llm), trace_sink
    )
    run = asyncio.run(workflow.run("TASK-RECOVERED", brief()))

    assert mcp_calls == [("get_clash_event_sample", {})]
    assert run.result.workflow_status == "WAITING_APPROVAL"
    assert run.result.revision_count == 1
    assert len(planner_calls) == 2
    assert planner_calls[1]["error_type"] == "mission_unreachable"
    healing = run.result.autohealing_events
    assert len(healing) == 1
    assert healing[0].recovery_action == "RUN_AGENT"
    assert healing[0].target == "event_planner_agent"
    assert healing[0].before["missions"][0]["target_count"] == 30
    assert healing[0].after["missions"][0]["target_count"] == 2
    assert healing[0].revalidation_status == "PASS"
    assert [(entry["actor"], entry["action"], entry["status"]) for entry in traces] == [
        ("mcp", "get_clash_event_sample", "SUCCESS"),
        ("event_planner_agent", "generate_plan", "SUCCESS"),
        ("balance_rule_agent", "validate_plan", "FAILED"),
        ("supervisor", "RUN_AGENT", "SUCCESS"),
        ("event_planner_agent", "regenerate_plan", "SUCCESS"),
        ("balance_rule_agent", "validate_plan", "SUCCESS"),
        ("critic_agent", "critic_review", "SUCCESS"),
        ("supervisor", "WAIT_APPROVAL", "SUCCESS"),
    ]
    assert traces[5]["phase"] == "revalidation"


def test_clash_balance_agent_rejection_is_replanned_before_critic():
    calls = []

    async def mcp_transport(_tool, _arguments):
        return sample()

    async def llm_transport(agent, _context):
        calls.append(agent)
        if agent == "event_planner_agent":
            return plan()
        if agent == "balance_rule_agent":
            return {
                "is_valid": calls.count("balance_rule_agent") > 1,
                "reason": "첫 안은 운영 목적과 맞지 않습니다.",
            }
        if agent == "critic_agent":
            return {"status": "PASS", "error_type": None, "reason": "검수 통과"}
        if agent == "supervisor":
            return {"approved": True, "reason": "승인 대기 가능"}
        raise AssertionError(agent)

    llm = LLMService(llm_transport, 5)
    workflow = ClashEventWorkflow(mcp_transport, llm, Supervisor({"context_access": {}}, llm))
    run = asyncio.run(workflow.run("TASK-BALANCE-RECOVERY", brief()))

    assert run.result.workflow_status == "WAITING_APPROVAL"
    assert run.result.revision_count == 1
    assert calls == [
        "event_planner_agent",
        "balance_rule_agent",
        "event_planner_agent",
        "balance_rule_agent",
        "critic_agent",
    ]
    assert run.result.autohealing_events[0].error_type == "event_brief_mismatch"
    assert run.result.autohealing_events[0].revalidation_status == "PASS"


def test_clash_repeated_invalid_plans_safe_stop_without_critic_review():
    traces = []
    planner_calls = 0

    async def mcp_transport(_tool, _arguments):
        return sample()

    async def llm_transport(agent, _context):
        nonlocal planner_calls
        if agent == "event_planner_agent":
            planner_calls += 1
            candidate = plan()
            candidate["missions"][0]["target_count"] = 30
            return candidate
        raise AssertionError(f"{agent} must not run after repeated validation failure")

    async def trace_sink(event):
        traces.append(event)

    llm = LLMService(llm_transport, 5)
    workflow = ClashEventWorkflow(
        mcp_transport, llm, Supervisor({"context_access": {}}, llm), trace_sink
    )
    run = asyncio.run(workflow.run("TASK-SAFE-STOP", brief()))

    assert run.result.workflow_status == "FAILED"
    assert run.result.current_result is None
    assert run.result.revision_count == 2
    assert planner_calls == 3
    assert [event.recovery_action for event in run.result.autohealing_events] == [
        "RUN_AGENT",
        "RUN_AGENT",
        "SAFE_STOP",
    ]
    assert [event.revalidation_status for event in run.result.autohealing_events] == [
        "FAILED",
        "FAILED",
        "NOT_RUN",
    ]
    assert traces[-1]["actor"] == "supervisor"
    assert traces[-1]["action"] == "SAFE_STOP"


def test_clash_mcp_failure_records_supervisor_safe_stop():
    traces = []

    async def mcp_transport(_tool, _arguments):
        raise ConnectionError("MCP unavailable")

    async def llm_transport(agent, _context):
        assert agent == "supervisor"
        return {"approved": True, "reason": "대체 조회 경로 없음"}

    async def trace_sink(event):
        traces.append(event)

    llm = LLMService(llm_transport, 5)
    workflow = ClashEventWorkflow(
        mcp_transport, llm, Supervisor({"context_access": {}}, llm), trace_sink
    )
    run = asyncio.run(workflow.run("TASK-MCP-FAILED", brief()))

    assert run.result.workflow_status == "FAILED"
    assert run.result.autohealing_events[0].recovery_action == "SAFE_STOP"
    assert run.result.autohealing_events[0].error_type == "mcp_timeout"
    assert [(item["actor"], item["action"]) for item in traces] == [
        ("mcp", "get_clash_event_sample"),
        ("supervisor", "SAFE_STOP"),
    ]


def test_clash_snapshot_drops_unexpected_identifiers():
    response = sample()
    response["sample"]["player_tag"] = "#PRIVATE"
    assert "player_tag" not in validate_sample(response)["sample"]


def test_clash_api_keeps_sample_through_approval_and_records_trace(client, monkeypatch):
    async def mcp_transport(_tool, _arguments):
        return sample()

    monkeypatch.setenv("MCP_URL", "http://127.0.0.1:8010/mcp")
    adapter = BackendBWorkflowAdapter("mcp")
    adapter._clash_workflow = ClashEventWorkflow(
        mcp_transport,
        LLMService(adapter._llm_transport, 5),
        adapter._engine._supervisor,
        adapter._save_trace,
    )
    monkeypatch.setattr(task_service, "workflow_engine", adapter)

    response = client.post(
        "/api/events/generate",
        json={
            "actor_id": "liveops01",
            "event_name": "공개 표본 원정",
            "target_user": "all_user",
            "duration": 2,
            "objective": "마을 원정 참여",
            "game_source": "clash_of_clans",
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 201
    task = response.json()
    assert task["workflow_status"] == "WAITING_APPROVAL"
    assert task["current_result"]["sample_summary"]["player_count"] == 20
    assert "clash_snapshot" not in task
    traces = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"]
    assert any(entry["action"] == "get_clash_event_sample" for entry in traces)
    assert {
        "event_planner_agent",
        "balance_rule_agent",
        "critic_agent",
        "supervisor",
    }.issubset({entry["actor"] for entry in traces})

    approved = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={"actor_id": "liveops01", "result_version": 1, "action": "APPROVE"},
    )
    assert approved.status_code == 200
    final = approved.json()
    assert final["workflow_status"] == "COMPLETED"
    assert final["final_result"]["game_source"] == "clash_of_clans"
    assert final["final_result"]["sample_summary"] == task["current_result"]["sample_summary"]


def test_clash_api_returns_recorded_autohealing_after_real_validation_failure(client, monkeypatch):
    calls = 0

    async def mcp_transport(_tool, _arguments):
        return sample()

    async def llm_transport(agent, _context):
        nonlocal calls
        if agent == "event_planner_agent":
            calls += 1
            candidate = plan()
            if calls == 1:
                candidate["missions"][0]["target_count"] = 30
            return candidate
        if agent == "balance_rule_agent":
            return {"is_valid": True, "reason": "검증된 기획안"}
        if agent == "critic_agent":
            return {"status": "PASS", "error_type": None, "reason": "검수 통과"}
        if agent == "supervisor":
            return {"approved": True, "reason": "승인 대기 가능"}
        raise AssertionError(agent)

    monkeypatch.setenv("MCP_URL", "http://127.0.0.1:8010/mcp")
    adapter = BackendBWorkflowAdapter("mcp")
    llm = LLMService(llm_transport, 5)
    adapter._clash_workflow = ClashEventWorkflow(
        mcp_transport, llm, Supervisor({"context_access": {}}, llm), adapter._save_trace
    )
    monkeypatch.setattr(task_service, "workflow_engine", adapter)

    response = client.post(
        "/api/events/generate",
        json={
            "actor_id": "liveops01",
            "event_name": "공개 표본 원정",
            "target_user": "all_user",
            "duration": 2,
            "objective": "마을 원정 참여",
            "game_source": "clash_of_clans",
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 201
    task = response.json()
    assert task["workflow_status"] == "WAITING_APPROVAL"
    assert task["revision_count"] == 1
    assert task["autohealing_events"][0]["before"]["missions"][0]["target_count"] == 30
    assert task["autohealing_events"][0]["after"]["missions"][0]["target_count"] == 2
    assert task["autohealing_events"][0]["revalidation_status"] == "PASS"
    traces = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"]
    assert [entry["action"] for entry in traces if entry["actor"] == "supervisor"] == [
        "RUN_AGENT",
        "WAIT_APPROVAL",
    ]
