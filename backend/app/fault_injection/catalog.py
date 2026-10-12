"""Single authority for selectable fault-lab boundaries and scenarios."""

from dataclasses import asdict
from itertools import pairwise

from backend.app.faults.error_catalog import ERROR_CATALOG

VERSION = "fault-lab-v1.3"
BOUNDARIES = {
    "legacy.planner.response": "backend.app.services.llm_service.LLMService.generate",
    "workflow.cas": "backend.app.services.workflow_execution.cancel",
    "scope.fixture": "backend.app.services.canonical_planning.create_event_workflow",
    "storage.write": "backend.app.services.workflow_execution._write_once",
    "trace.write": "backend.app.services.workflow_execution.record_observation",
    "storage.commit_ack": "backend.app.services.canonical_planning._persist_record",
    "workflow.deadline": "backend.app.services.workflow_execution.run_async",
    "context.validate": "backend.app.agents.supervisor.Supervisor.build_agent_context",
    "tool.validate": "backend.app.services.mcp_client.MCPClient.call_tool",
    "planner.call": "backend.app.services.llm_service.LLMService.generate",
    "planner.response": "backend.app.services.llm_service.LLMService.generate",
    "critic.call": "backend.app.services.llm_service.LLMService.generate",
    "critic.response": "backend.app.services.llm_service.LLMService.generate",
    "handoff.validate": "backend.app.services.workflow_execution.validate_handoff",
    "workflow.execute": "backend.app.services.workflow_execution._execute",
    "mcp.get_event_capabilities.call": "backend.app.services.mcp_client.MCPClient.call_tool",
    "mcp.get_event_capabilities.response": "backend.app.services.mcp_client.MCPClient.call_tool",
    "mcp.get_game_rules.call": "backend.app.services.mcp_client.MCPClient.call_tool",
}
READY = {
    "event_brief_mismatch": "legacy.planner.response",
    "mission_unreachable": "legacy.planner.response",
    "item_not_found": "legacy.planner.response",
    "supervisor_state_conflict": "workflow.cas",
    "recovery_limit_exceeded": "planner.response",
    "unsupported_capability": "scope.fixture",
    "game_data_unavailable": "scope.fixture",
    "db_connection_error": "storage.write",
    "db_write_outcome_unknown": "storage.commit_ack",
    "trace_write_error": "trace.write",
    "workflow_deadline_exceeded": "workflow.deadline",
    "context_guard_blocked": "context.validate",
    "tool_permission_denied": "tool.validate",
    "response_guard_blocked": "planner.response",
    "planner_output_invalid": "planner.response",
    "critic_output_invalid": "critic.response",
    "planner_explanation_mismatch": "critic.response",
    "llm_timeout": "planner.call",
    "llm_execution_error": "planner.call",
    "llm_budget_exceeded": "planner.call",
    "handoff_contract_invalid": "handoff.validate",
    "unknown_error": "workflow.execute",
    "mcp_timeout": "mcp.get_event_capabilities.call",
    "mcp_tool_error": "mcp.get_event_capabilities.call",
    "mcp_game_rules_error": "mcp.get_game_rules.call",
    "mcp_response_invalid": "mcp.get_event_capabilities.response",
}


def catalog() -> dict:
    nodes = [
        (
            "supervisor",
            "Supervisor · 상태·복구 결정",
            "supervisor",
            ["workflow.execute", "workflow.deadline", "workflow.cas"],
        ),
        (
            "mcp",
            "게임 규칙·지원 범위 조회",
            "process",
            [
                "mcp.get_event_capabilities.call",
                "mcp.get_event_capabilities.response",
                "mcp.get_game_rules.call",
            ],
        ),
        (
            "handoff",
            "권한·컨텍스트·인계",
            "validation",
            ["handoff.validate", "context.validate", "tool.validate"],
        ),
        ("planner", "기획 Agent", "agent", ["planner.call", "planner.response"]),
        ("validation", "규칙·경제·의미 검증", "validation", ["scope.fixture"]),
        ("critic", "검수·피드백 Agent", "agent", ["critic.call", "critic.response"]),
        (
            "store",
            "기획안·Trace 저장",
            "storage",
            ["storage.write", "trace.write", "storage.commit_ack"],
        ),
        ("approval", "사람 승인 대기 / 안전 종료", "terminal", []),
    ]
    scenarios = []
    for fault_id, definition in ERROR_CATALOG.items():
        if fault_id in {"input_validation_error", "input_guard_blocked"}:
            continue
        target = READY.get(fault_id)
        scenarios.append(
            {
                "scenario_id": fault_id,
                "version": VERSION,
                "fault_id": fault_id,
                **asdict(definition),
                "target_id": target,
                "readiness": "ready" if target else "external_runner_required",
                "reason": None if target else "전용 fixture/저장·프로세스 검증 runner 연결 필요",
                "route": "canonical_planning",
                "fixture_id": "event-dungeon-gold",
                "fixture_version": "opong-data-1.5.0",
                "expected_profile_version": VERSION,
                "expected_detection": {
                    "error_code": definition.code,
                    "source": "actual_trace_or_catch",
                },
                "expected_actions": "pinned_policy_snapshot_with_boundary_safety_rules",
                "attempt_bounds": {"minimum": 1, "maximum": 4},
                "expected_terminal": "scenario_oracle_v1",
                "fixed_input_checks": [
                    "objective",
                    "schedule",
                    "source_game_version",
                    "actions",
                    "rewards",
                    "mission_reward_links",
                ],
                "forbidden_calls": ["game.events.apply", "game.rewards.grant"],
                "expected_db_effects": {"runtime": "unchanged", "planning": "run_owned_only"},
                "required_evidence": [
                    "injection_journal",
                    "detection_journal",
                    "policy_ref",
                    "runtime_snapshot",
                ],
                "patterns": ["once", "first_n", "always"]
                if definition.retry_class != "NEVER"
                else ["once"],
                "profiles": ["current_active", "safe_stop"]
                + (["retry_allowed"] if definition.retry_class != "NEVER" else []),
            }
        )
        row = scenarios[-1]
        if target == "legacy.planner.response":
            row.update(
                route="legacy_task",
                fixture_id="legacy-tinyheroes-growth",
                fixture_version="tinyheroes-1.0.0",
            )
        if fault_id == "recovery_limit_exceeded":
            row.update(
                patterns=["always"],
                profiles=["current_active"],
                observed_code=ERROR_CATALOG["planner_output_invalid"].code,
                note="복구 한도 종료는 원인 오류 코드를 유지한다. LIMIT 코드를 강제로 발생시키지 않는다.",
            )
        if target in {
            "scope.fixture",
            "workflow.cas",
            "storage.commit_ack",
            "trace.write",
            "storage.write",
        }:
            row["patterns"] = ["once"]
            row["profiles"] = ["current_active", "safe_stop"]
    topology = {
        "topology_id": "canonical_planning",
        "version": VERSION,
        "route": "canonical_planning",
        "nodes": [
            {
                "node_id": id_,
                "label": label,
                "kind": kind,
                "target_ids": targets,
                "observed_events": {
                    "validation": [
                        "capability.scope_checked",
                        "rule_balance.validation_completed",
                        "final_consistency.checked",
                    ],
                    "approval": ["approval.pending", "workflow.safe_stopped"],
                    "planner": ["planner.started", "planner.output_validated"],
                    "critic": ["critic.review_started", "critic.review_completed"],
                    "store": ["storage.result_persisted"],
                    "mcp": [
                        "runtime.evidence_collected",
                        "mcp.recovery_decided",
                        "mcp.recovery_resolved",
                    ],
                    "supervisor": [
                        "brief.accepted",
                        "supervisor.state_transition_decided",
                        "agent.recovery_decided",
                        "agent.recovery_resolved",
                        "storage.recovery_decided",
                        "storage.recovery_resolved",
                        "workflow.safe_stopped",
                    ],
                    "handoff": ["handoff.requested", "handoff.accepted", "handoff.rejected"],
                }.get(id_, []),
            }
            for id_, label, kind, targets in nodes
        ],
        "edges": [
            {"from": a, "to": b, "kind": "flow"}
            for a, b in zip(
                [n[0] for n in nodes if n[0] != "supervisor"][:-1],
                [n[0] for n in nodes if n[0] != "supervisor"][1:],
                strict=True,
            )
        ]
        + [
            {"from": "critic", "to": "planner", "kind": "revision"},
            {"from": "supervisor", "to": "planner", "kind": "recovery"},
            {"from": "supervisor", "to": "mcp", "kind": "recovery"},
        ],
    }
    fallback = next(s for s in scenarios if s["fault_id"] == "mcp_game_rules_error")
    fallback.update(
        patterns=["once"],
        aliases=["mcp_tool_error.rules"],
        note="Primary 게임 규칙 조회 실패 후 digest가 일치하는 실제 fallback 규칙을 조회한다.",
    )
    targets = [
        {
            "target_id": target,
            "boundary_ref": ref,
            "adapter_kind": "context_hook",
            "supported_fault_ids": sorted(
                {s["fault_id"] for s in scenarios if s["target_id"] == target}
            ),
            "readiness": "ready",
        }
        for target, ref in BOUNDARIES.items()
    ]
    data = {
        "version": VERSION,
        "scenarios": scenarios,
        "topologies": [
            topology,
            {
                "topology_id": "legacy_task",
                "version": VERSION,
                "route": "legacy_task",
                "nodes": [
                    {
                        "node_id": "legacy_input",
                        "label": "기존 Task 요청",
                        "kind": "validation",
                        "target_ids": [],
                        "observed_events": ["create_task"],
                    },
                    {
                        "node_id": "legacy_planner",
                        "label": "기존 기획 Agent",
                        "kind": "agent",
                        "target_ids": ["legacy.planner.response"],
                        "observed_events": ["create_plan", "regenerate_plan"],
                    },
                    {
                        "node_id": "legacy_rules",
                        "label": "게임 규칙·보상 검사",
                        "kind": "validation",
                        "target_ids": [],
                        "observed_events": [
                            "validate_plan",
                            "get_event_planning_context",
                            "get_game_rules",
                            "get_item_info",
                        ],
                    },
                    {
                        "node_id": "legacy_critic",
                        "label": "검수 Agent",
                        "kind": "agent",
                        "target_ids": [],
                        "observed_events": ["final_review"],
                    },
                    {
                        "node_id": "legacy_supervisor",
                        "label": "Supervisor 정책·수정·종료",
                        "kind": "supervisor",
                        "target_ids": [],
                        "observed_events": [
                            "supervisor.state_transition_decided",
                            "RUN_AGENT",
                            "WAIT_APPROVAL",
                            "WAITING_APPROVAL",
                            "SAFE_STOP",
                            "safe_stop",
                            "HANDOFF_REQUESTED",
                            "HANDOFF_ACCEPTED",
                        ],
                    },
                ],
                "edges": [
                    {"from": a, "to": b, "kind": "flow"}
                    for a, b in [
                        ("legacy_input", "legacy_planner"),
                        ("legacy_planner", "legacy_rules"),
                        ("legacy_rules", "legacy_critic"),
                        ("legacy_rules", "legacy_supervisor"),
                        ("legacy_supervisor", "legacy_planner"),
                    ]
                ],
            },
            {
                "topology_id": "approved_runtime",
                "version": VERSION,
                "route": "approved_runtime",
                "readiness": "graph_only",
                "note": "승인 후 쓰기 장애 fixture는 별도 runner 확장 범위다.",
                "nodes": [
                    {
                        "node_id": id_,
                        "label": label,
                        "kind": kind,
                        "target_ids": [],
                        "observed_events": [],
                    }
                    for id_, label, kind in [
                        ("runtime_approval", "사람 승인 확인", "validation"),
                        ("runtime_contract", "버전·입력·멱등 계약", "validation"),
                        ("runtime_transaction", "게임 DB transaction", "storage"),
                        ("runtime_register", "선택 이벤트 공간 등록", "process"),
                        ("runtime_trace", "반영 결과·Trace", "storage"),
                        ("runtime_mission", "행동·미션 판정", "validation"),
                        ("runtime_reward", "첫 클리어 보상 기록", "storage"),
                    ]
                ],
                "edges": [
                    {"from": a, "to": b, "kind": "flow"}
                    for a, b in [
                        ("runtime_approval", "runtime_contract"),
                        ("runtime_contract", "runtime_transaction"),
                        ("runtime_transaction", "runtime_register"),
                        ("runtime_register", "runtime_trace"),
                        ("runtime_trace", "runtime_mission"),
                        ("runtime_mission", "runtime_reward"),
                    ]
                ],
            },
        ],
        "targets": targets,
        "provider_mode": "deterministic",
        "database": "eventquest_fault_test",
    }
    _fixed_graphs(data)
    return data


def _fixed_graphs(data: dict) -> None:
    """Presentation coordinates remain unchanged across requests and retries."""
    canonical = data["topologies"][0]
    labels = {
        "mcp": "게임 데이터 조회",
        "handoff": "권한·컨텍스트·인계",
        "planner": "기획안 작성",
        "validation": "게임 규칙·보상 검증",
        "critic": "기획안 검수",
        "store": "기획안·Trace 저장",
        "approval": "사람 승인 대기",
    }
    for node in canonical["nodes"]:
        node["label"] = labels.get(node["node_id"], node["label"])
        if node["node_id"] == "validation":
            node["observed_events"].remove("final_consistency.checked")
        if node["node_id"] == "approval":
            node["observed_events"] = ["approval.pending"]
    canonical["nodes"] += [
        {
            "node_id": "final",
            "label": "최종 일관성 확인",
            "kind": "validation",
            "target_ids": [],
            "observed_events": ["final_consistency.checked"],
        },
        {
            "node_id": "safe_stop",
            "label": "안전 중단",
            "kind": "terminal",
            "target_ids": [],
            "observed_events": ["workflow.safe_stopped"],
        },
    ]
    positions = {
        "mcp": (24, 64),
        "handoff": (24, 150),
        "planner": (24, 236),
        "validation": (348, 236),
        "critic": (348, 322),
        "final": (24, 408),
        "store": (24, 494),
        "approval": (24, 580),
        "supervisor": (672, 150),
        "safe_stop": (672, 494),
    }
    flow = [
        "mcp",
        "handoff",
        "planner",
        "validation",
        "critic",
        "final",
        "store",
        "approval",
    ]
    canonical["edges"] = [
        {"from": a, "to": b, "kind": "flow", "label": "정상 진행"} for a, b in pairwise(flow)
    ]
    canonical["edges"] += [
        {"from": "critic", "to": "planner", "kind": "revision", "label": "설명 수정"},
        {"from": "planner", "to": "supervisor", "kind": "control", "label": "오류·복구 판단"},
        {"from": "supervisor", "to": "planner", "kind": "recovery", "label": "재시도·수정"},
        {"from": "supervisor", "to": "mcp", "kind": "recovery", "label": "재조회·대체 조회"},
        {
            "from": "supervisor",
            "to": "safe_stop",
            "kind": "stop",
            "label": "복구 불가·한도·보안 차단",
        },
    ]
    for topology in data["topologies"]:
        topology["layout"] = {
            "version": "fixed-graph-2",
            "width": 980,
            "height": 672 if topology is canonical else 64 + 86 * len(topology["nodes"]),
            "lanes": [
                {"x": 24, "label": "주 흐름"},
                {"x": 348, "label": "검증·평가"},
                {"x": 672, "label": "Supervisor · 상태·전이·복구"},
            ],
        }
        for i, node in enumerate(topology["nodes"]):
            x, y = positions[node["node_id"]] if topology is canonical else (24, 64 + i * 86)
            node["position"] = {"x": x, "y": y, "width": 280, "height": 68}
    target_labels = {
        "workflow.execute": "Workflow 실행",
        "workflow.deadline": "전체 실행 시간 제한",
        "workflow.cas": "상태 전이 충돌 검사",
        "context.validate": "Agent 컨텍스트 검사",
        "tool.validate": "Tool 권한 검사",
        "handoff.validate": "Agent 인계 계약 검사",
        "planner.call": "기획 Agent LLM 호출",
        "planner.response": "기획 Agent 응답 검사",
        "critic.call": "검수 Agent LLM 호출",
        "critic.response": "검수 Agent 응답 검사",
        "scope.fixture": "게임 지원 범위·자료 검사",
        "storage.write": "실행 상태 저장",
        "trace.write": "필수 Trace 저장",
        "storage.commit_ack": "기획 결과 저장 확인",
        "legacy.planner.response": "기존 Task 기획 Agent 응답 검사",
        "mcp.get_event_capabilities.call": "MCP 지원 기능 조회",
        "mcp.get_event_capabilities.response": "MCP 지원 기능 응답 검사",
        "mcp.get_game_rules.call": "MCP 게임 규칙 조회",
    }
    for target in data["targets"]:
        target["label"] = target_labels[target["target_id"]]


def validate_registry() -> None:
    import importlib

    data = catalog()
    assert {row["fault_id"] for row in data["scenarios"]} == set(ERROR_CATALOG) - {
        "input_validation_error",
        "input_guard_blocked",
    }
    targets = {row["target_id"] for row in data["targets"]}
    assert len(targets) == len(data["targets"])
    for topology in data["topologies"]:
        ids = {n["node_id"] for n in topology["nodes"]}
        assert len(ids) == len(topology["nodes"])
        assert all(e["from"] in ids and e["to"] in ids for e in topology["edges"])
        assert all(t in targets for n in topology["nodes"] for t in n["target_ids"])
    for ref in BOUNDARIES.values():
        parts = ref.split(".")
        for index in range(len(parts) - 1, 0, -1):
            try:
                value = importlib.import_module(".".join(parts[:index]))
            except ModuleNotFoundError:
                continue
            for attr in parts[index:]:
                value = getattr(value, attr)
            assert callable(value)
            break
        else:
            raise AssertionError(f"Unregistered boundary: {ref}")
