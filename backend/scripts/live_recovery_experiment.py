"""Prepare by default; explicit CLI-only live execution with shared lab fencing."""

import argparse
import asyncio
import json
import os
import socket
import time
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from backend.app.repositories import fault_lab as store
from backend.app.schemas.planning import CapabilityCatalog, EventBriefInput
from backend.app.services import canonical_planning
from backend.app.services import recovery_policy as policies
from backend.app.services.canonical_agents import build_canonical_agents
from backend.app.services.llm_service import _OUTPUT_SCHEMAS, workflow_llm_call_budget
from backend.app.services.mcp_client import create_streamable_http_transport
from backend.app.services.workflow_execution import read_progress, submit
from backend.scripts.prepare_fault_precision import source_identity
from backend.tests.support.fault_lab_precision import precision_checks
from backend.tests.support.fault_lab_runner import oracle, runtime_snapshot
from backend.tests.support.live_recovery import (
    CASES,
    CampaignBudget,
    digest,
    fixture,
    validate_result,
)
from backend.tests.support.mcp_fault_proxy import fault_proxy
from dotenv import load_dotenv
from mcp_server.tools._opong_data import GAME_ID, load_opong_snapshot
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "backend/test-results/fault-lab/live"


def prepare() -> dict:
    if os.getenv("CANONICAL_PLANNING_MODE") != "live":
        raise ValueError("EXPLICIT_LIVE_CONFIGURATION_REQUIRED")
    engine = store.database_engine()
    engine.dispose()
    if store.environment()["state"] != "CLEAN":
        raise ValueError("LAB_CLEAN_REQUIRED")
    if os.getenv("MCP_URL") != "http://127.0.0.1:8014/mcp":
        raise ValueError("OWNED_MCP_8014_REQUIRED")
    with socket.create_connection(("127.0.0.1", 8014), timeout=5):
        pass
    transport = create_streamable_http_transport(os.environ["MCP_URL"])

    async def probe():
        return await asyncio.wait_for(
            transport(
                "get_event_capabilities",
                {
                    "game_id": GAME_ID,
                    "game_data_snapshot_id": fixture("normal_single")["source_game_version"],
                },
            ),
            timeout=10,
        )

    response = asyncio.run(probe())
    capabilities = CapabilityCatalog.model_validate(
        {k: v for k, v in response.items() if k not in {"source", "snapshot_digest"}}
    ).model_dump(mode="json")
    snapshot_id = fixture("normal_single")["source_game_version"]
    if (
        response.get("snapshot_digest") != load_opong_snapshot(snapshot_id)["snapshot_digest"]
        or capabilities["game_context"]["game_data_snapshot_id"] != snapshot_id
    ):
        raise ValueError("MCP_SNAPSHOT_MISMATCH")
    build_canonical_agents()  # Construct configured services; no provider traffic.
    with workflow_llm_call_budget():  # Validate budgets, encryption key and DB migration.
        pass
    for name in CASES:
        EventBriefInput.model_validate(fixture(name))
    policy = next(v for v in policies.list_policies()["versions"] if v["status"] == "ACTIVE")
    for code in [
        "ERR-CONTRACT-PLANNER-001",
        "ERR-DOMAIN-PLANNER-002",
        "ERR-DEP-LLM-001",
        "ERR-DEP-MCP-001",
    ]:
        rule = policy["policy"]["routes"][code]
        if (
            rule["action"] not in {"RETRY_AGENT", "RETRY_OPERATION", "REVISE_PLAN"}
            or rule["max_attempts"] < 2
        ):
            raise ValueError("LIVE_RECOVERY_POLICY_NOT_ENABLED")
    snapshot = runtime_snapshot()
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "PREPARED_NOT_EXECUTED",
        "source": source_identity(),
        "model": os.environ["OPENAI_MODEL"],
        "pricing_version": os.environ["LLM_PRICING_VERSION"],
        "policy_version": policy["version"],
        "policy_digest": digest(policy["policy"]),
        "fixture_digests": {k: digest(fixture(k)) for k in CASES},
        "runtime_digest": digest(snapshot),
        "capability_digest": digest(capabilities),
        "provider_calls": 0,
        "database": "eventquest_fault_test",
        "note": "No provider calls. Key authentication, price accuracy and actual generation unverified.",
    }


def probe_wire() -> list[dict]:
    """Read-only SDK/HTTP readiness, not a Supervisor recovery experiment."""

    async def run():
        output = []
        arguments = {
            "game_id": GAME_ID,
            "game_data_snapshot_id": fixture("normal_single")["source_game_version"],
        }
        for mode in ["http503_once", "disconnect_once", "delay_once", "http503_always"]:
            with fault_proxy(os.environ["MCP_URL"], mode) as (endpoint, journal):
                transport = create_streamable_http_transport(endpoint)
                attempts = []
                for _ in range(2):
                    try:
                        response = await asyncio.wait_for(
                            transport("get_event_capabilities", arguments), timeout=5
                        )
                        attempts.append(
                            "SUCCESS"
                            if response.get("game_context", {}).get("game_data_snapshot_id")
                            == arguments["game_data_snapshot_id"]
                            else "SNAPSHOT_MISMATCH"
                        )
                    except (ConnectionError, TimeoutError) as exc:
                        attempts.append(type(exc).__name__)
                expected = attempts[0] != "SUCCESS" and (
                    attempts[1] != "SUCCESS"
                    if mode == "http503_always"
                    else attempts[1] == "SUCCESS"
                )
                output.append(
                    {
                        "mode": mode,
                        "attempts": attempts,
                        "passed": expected,
                        "wire_journal": journal,
                    }
                )
        return output

    with store.lease("wire-preflight-" + str(uuid4())):
        return asyncio.run(run())


@contextmanager
def guarded_agents(budget):
    original = canonical_planning.build_canonical_agents
    workers = yaml.safe_load((ROOT / "backend/app/config/agents.yaml").read_text(encoding="utf-8"))[
        "workers"
    ]

    def factory():
        planner, critic, supervisor, profile = build_canonical_agents()
        service = planner._llm_service
        transport = service._transport

        async def guarded(agent, context):
            return await budget.invoke(
                transport,
                agent,
                context,
                instructions=workers[agent]["instructions"],
                schema=_OUTPUT_SCHEMAS[agent],
            )

        service._transport = guarded
        return planner, critic, supervisor, profile

    canonical_planning.build_canonical_agents = factory
    try:
        yield
    finally:
        canonical_planning.build_canonical_agents = original


def execute(case_id: str, campaign_id: str, *, max_calls: int, max_cost: str) -> dict:
    from backend.app.fault_injection.catalog import catalog
    from backend.app.fault_injection.injector import injection_context

    baseline = prepare()
    case = CASES[case_id]
    budget_path = OUTPUT / f"campaign-{UUID(campaign_id)!s}.json"
    CampaignBudget(budget_path, max_calls=max_calls, max_cost=max_cost)  # Validate before queueing.
    owner = "live-" + str(uuid4())
    record, _ = store.create("run", {"scenario_id": case_id, "provider_mode": "live"}, owner)
    with store.lease(owner):
        budget = CampaignBudget(budget_path, max_calls=max_calls, max_cost=max_cost)
        record.update(
            lifecycle="RUNNING", started_at=datetime.now(UTC).isoformat(), baseline=baseline
        )
        store.save(record["id"], record)
        print("LIVE_RUN " + record["id"], flush=True)
        started = time.monotonic()
        journal = []

        def emit(row):
            journal.append({**row, "elapsed_monotonic_seconds": time.monotonic() - started})

        before = runtime_snapshot()
        key = "live-" + record["id"]
        original_mcp = os.environ["MCP_URL"]
        first_attempt = len(budget.data["attempts"])
        result = {
            "verdict": "INCONCLUSIVE",
            "provider_mode": "live",
            "experiment_kind": case["kind"],
            "scenario": {
                "scenario_id": case_id,
                "fault_id": case_id,
                "target_id": "planner.response",
                "route": "canonical_planning",
                "safe_message": "실호출 시험",
                "code": "UNCONFIRMED",
            },
        }
        try:
            with policies.pin_active_policy() as pinned, guarded_agents(budget):
                scenario = next(
                    (
                        s
                        for s in catalog()["scenarios"]
                        if s["scenario_id"] == case.get("scenario_id")
                    ),
                    None,
                )
                request = {
                    "scenario_id": case.get("scenario_id", case_id),
                    "pattern": case.get("pattern", "once"),
                    "first_n": 1,
                    "profile": "current_active",
                }
                # Normal/wire contexts observe transitions without perturbing real data.
                context = injection_context(
                    record["id"],
                    request,
                    scenario["target_id"] if scenario else "observer.only",
                    emit,
                )
                wire = (
                    fault_proxy(original_mcp, case["mode"])
                    if case["kind"] == "wire"
                    else nullcontext((original_mcp, []))
                )
                with wire as (endpoint, wire_log), context:
                    os.environ["MCP_URL"] = endpoint
                    workflow = submit(
                        EventBriefInput.model_validate(fixture(case_id)), key, asynchronous=False
                    )
                finished = time.monotonic() - started
                checks = validate_result(case_id, fixture(case_id), workflow)
                observation_scenario = scenario or {
                    "scenario_id": case_id,
                    "target_id": "scope.fixture"
                    if case["kind"] == "normal"
                    else "mcp.get_event_capabilities.call",
                    "route": "canonical_planning",
                    "code": "ERR-DEP-MCP-001",
                }
                checks.extend(precision_checks(observation_scenario, pinned, workflow, journal))
                checks.append(
                    {"name": "runtime_unchanged", "expected": before, "actual": runtime_snapshot()}
                )
                checks.append(
                    {
                        "name": "stored_result",
                        "expected": workflow,
                        "actual": read_progress(workflow["workflow_id"]),
                    }
                )
                replay_before = len(budget.data["attempts"])
                replay = submit(
                    EventBriefInput.model_validate(fixture(case_id)), key, asynchronous=False
                )
                checks.append(
                    {
                        "name": "idempotent_workflow_replay",
                        "expected": workflow["workflow_id"],
                        "actual": replay["workflow_id"],
                    }
                )
                checks.append(
                    {
                        "name": "replay_provider_calls",
                        "expected": 0,
                        "actual": len(budget.data["attempts"]) - replay_before,
                    }
                )
                attempts = budget.data["attempts"][first_attempt:]
                with store.connection_scope() as connection:
                    db_records = connection.execute(
                        text("SELECT count(*) FROM llm_prompt_records WHERE workflow_id=:id"),
                        {"id": workflow["workflow_id"]},
                    ).scalar_one()
                measured = sum(a["status"] == "MEASURED" for a in attempts)
                checks.append(
                    {
                        "name": "encrypted_provider_records",
                        "expected": measured,
                        "actual": db_records,
                    }
                )
                live_expected = case_id not in {"mcp_503_always", "llm_timeout_always"}
                checks.append(
                    {
                        "name": "actual_provider_calls",
                        "expected": live_expected,
                        "actual": measured > 0,
                    }
                )
                recovery = [
                    t
                    for t in workflow.get("trace", [])
                    if t.get("error_code") and ("decided" in t.get("event", "") or t.get("action"))
                ]
                if scenario:
                    injections = [
                        j for j in journal if j.get("kind") == "injection" and j.get("applied")
                    ]
                    checks.append(
                        {
                            "name": "injection_applied",
                            "expected": True,
                            "actual": len(injections) >= 2
                            if case.get("pattern") == "always"
                            else len(injections) == 1,
                        }
                    )
                    checks.append(
                        {
                            "name": "requested_error_detected",
                            "expected": True,
                            "actual": any(
                                t.get("error_code") == scenario["code"] for t in recovery
                            ),
                        }
                    )
                    if case_id == "llm_timeout_once":
                        # Timeout is injected before traffic; subsequent Planner must really run.
                        minimum = 1
                    else:
                        minimum = 2
                    role = (
                        "canonical_event_critic_agent"
                        if case_id == "planner_explanation_once"
                        else "canonical_event_planner_agent"
                    )
                    checks.append(
                        {
                            "name": "real_recovery_invocations",
                            "expected": True,
                            "actual": sum(a["agent"] == role for a in attempts) >= minimum
                            if live_expected
                            else measured == 0,
                        }
                    )
                    independent = oracle(
                        request, scenario, pinned, journal, workflow, before, runtime_snapshot()
                    )
                    checks.extend(
                        c
                        for c in independent["checks"]
                        if c["name"]
                        in {
                            "policy_recovery_actions",
                            "target_attempts",
                            "forbidden_runtime_calls",
                            "critical_trace_mapping",
                        }
                    )
                if case["kind"] == "wire":
                    checks.append(
                        {
                            "name": "wire_failure_observed",
                            "expected": True,
                            "actual": any(
                                t.get("error_code") == "ERR-DEP-MCP-001" for t in recovery
                            ),
                        }
                    )
                    checks.append(
                        {
                            "name": "wire_injection",
                            "expected": True,
                            "actual": any("wire_fault" in t for t in wire_log),
                        }
                    )
                    if case_id != "mcp_503_always":
                        checks.append(
                            {
                                "name": "wire_recovered",
                                "expected": True,
                                "actual": any(t.get("upstream_status") == 200 for t in wire_log),
                            }
                        )
                if case["kind"] != "normal":
                    checks.append(
                        {"name": "detector_evidence", "expected": True, "actual": bool(recovery)}
                    )
                for c in checks:
                    c["passed"] = c["expected"] == c["actual"]
                detected = [
                    j["elapsed_monotonic_seconds"] for j in journal if j.get("kind") == "detection"
                ]
                result.update(
                    verdict="PASS" if all(c["passed"] for c in checks) else "FAIL",
                    checks=checks,
                    workflow_id=workflow["workflow_id"],
                    actual_terminal=workflow["workflow_status"],
                    trace=workflow.get("trace", []),
                    workflow=workflow,
                    injection_journal=journal,
                    boundary_calls=sum(j.get("kind") == "injection" for j in journal),
                    attempts=sum(
                        j.get("kind") == "injection"
                        and j.get("target_id") == observation_scenario["target_id"]
                        for j in journal
                    ),
                    retries=sum(
                        t.get("event") in {"agent.recovery_decided", "mcp.recovery_decided"}
                        and (t.get("action") or t.get("status"))
                        in {"RETRY", "RETRY_AGENT", "RETRY_OPERATION"}
                        for t in workflow.get("trace", [])
                    ),
                    plan_revisions=max(
                        [t.get("revision_count", 0) or 0 for t in workflow.get("trace", [])] or [0]
                    ),
                    fallback_calls=sum(
                        j.get("kind") == "injection" and "fallback" in j.get("target_id", "")
                        for j in journal
                    ),
                    wire_journal=wire_log,
                    provider_attempts=attempts,
                    policy_ref={k: v for k, v in pinned.items() if k != "routes"},
                    recovered=all(c["passed"] for c in checks)
                    and case["kind"] != "normal"
                    and workflow["workflow_status"] == "WAITING_APPROVAL",
                    detection_to_completion_seconds=finished - min(detected) if detected else None,
                    scenario=scenario
                    or {
                        "scenario_id": case_id,
                        "fault_id": case_id,
                        "code": "NORMAL" if case["kind"] == "normal" else "ERR-DEP-MCP-001",
                        "safe_message": "실제 LLM 정상 기획"
                        if case["kind"] == "normal"
                        else "실제 MCP 통신 복구",
                        "target_id": "planner.response"
                        if case["kind"] == "normal"
                        else "mcp.get_event_capabilities.call",
                        "route": "canonical_planning",
                    },
                )
        except Exception as exc:  # noqa: BLE001 - finalize evidence without exposing secret exception text.
            result.update(error=type(exc).__name__)  # Never expose provider/DB exception text.
        finally:
            os.environ["MCP_URL"] = original_mcp
            active = next(
                v for v in policies.list_policies()["versions"] if v["status"] == "ACTIVE"
            )
            cleanup = (
                active["version"] == baseline["policy_version"]
                and digest(active["policy"]) == baseline["policy_digest"]
                and runtime_snapshot() == before
            )
            if not cleanup:
                store.dirty({"reason": "LIVE_POLICY_OR_RUNTIME_CHANGED", "run_id": record["id"]})
                result["verdict"] = "INCONCLUSIVE"
            result.update(cleanup_ok=cleanup, elapsed_seconds=time.monotonic() - started)
            result.setdefault("provider_attempts", budget.data["attempts"][first_attempt:])
            record.update(
                lifecycle="COMPLETED",
                verdict=result["verdict"],
                results=[result],
                finished_at=datetime.now(UTC).isoformat(),
                campaign_id=campaign_id,
            )
            store.save(record["id"], record)
            OUTPUT.mkdir(parents=True, exist_ok=True)
            (OUTPUT / f"run-{record['id']}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return record


def main():
    load_dotenv(ROOT / "backend/.env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-live-llm", action="store_true")
    parser.add_argument("--probe-wire", action="store_true")
    parser.add_argument("--case", choices=tuple(CASES))
    parser.add_argument("--campaign-id")
    parser.add_argument("--max-calls", type=int, default=90)
    parser.add_argument("--max-cost", default="3.00")
    args = parser.parse_args()
    if args.execute and not (args.allow_live_llm and args.case and args.campaign_id):
        parser.error("Execution requires --allow-live-llm, --case and --campaign-id")
    try:
        if args.execute:
            record = execute(
                args.case, args.campaign_id, max_calls=args.max_calls, max_cost=args.max_cost
            )
            print("VERDICT " + record["verdict"])
            return 0 if record["verdict"] == "PASS" else 1
        baseline = prepare()
        if args.probe_wire:
            baseline["wire_readiness"] = probe_wire()
            baseline["status"] = (
                "PREPARED_NOT_EXECUTED"
                if all(r["passed"] for r in baseline["wire_readiness"])
                else "WIRE_PREPARATION_FAILED"
            )
        active = next(v for v in policies.list_policies()["versions"] if v["status"] == "ACTIVE")
        baseline["cleanup_ok"] = (
            store.environment()["state"] == "CLEAN"
            and active["version"] == baseline["policy_version"]
            and digest(active["policy"]) == baseline["policy_digest"]
            and digest(runtime_snapshot()) == baseline["runtime_digest"]
        )
        if not baseline["cleanup_ok"]:
            baseline["status"] = "PREPARATION_RESTORE_CHECK_FAILED"
        budget = CampaignBudget(
            OUTPUT / f"preflight-budget-{uuid4()}.json",
            max_calls=args.max_calls,
            max_cost=args.max_cost,
        )
        baseline["campaign_budget"] = budget.identity
        baseline["worst_call_reservation_usd"] = str(budget.unit_reservation)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        path = OUTPUT / f"preflight-{uuid4()}.json"
        path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PREPARED: " + str(path))
        return 0 if baseline["status"] == "PREPARED_NOT_EXECUTED" else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports only safe exception types.
        print("PREPARATION_OR_EXECUTION_FAILED: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
