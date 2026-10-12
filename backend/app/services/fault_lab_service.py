"""Isolated sequential subprocess execution with durable, independent evidence."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from backend.app.fault_injection.catalog import catalog
from backend.app.repositories import fault_lab as store

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fault-lab")
JOURNAL_ROOT = Path(
    os.getenv("FAULT_LAB_JOURNAL_DIR")
    or Path(__file__).resolve().parents[2] / "test-results" / "fault-lab"
)


def journal_path(id_: str, suffix: str = "jsonl") -> Path:
    canonical = str(UUID(id_))
    JOURNAL_ROOT.mkdir(parents=True, exist_ok=True)
    return JOURNAL_ROOT / f"{canonical}.{suffix}"


def validate_request(request: dict) -> dict:
    if os.getenv("CANONICAL_PLANNING_MODE", "deterministic") != "deterministic":
        raise store.LabError("FAULT_LAB_REQUIRES_DETERMINISTIC_PROVIDER", 403)
    scenario = next(
        (
            s
            for s in catalog()["scenarios"]
            if s["scenario_id"] == request["scenario_id"]
            or request["scenario_id"] in s.get("aliases", [])
        ),
        None,
    )
    if scenario is None:
        raise store.LabError("UNKNOWN_SCENARIO", 422)
    if scenario["readiness"] != "ready":
        raise store.LabError("SCENARIO_EXTERNAL_RUNNER_REQUIRED", 422)
    if (
        request["pattern"] not in scenario["patterns"]
        or request["profile"] not in scenario["profiles"]
    ):
        raise store.LabError("UNSUPPORTED_FAULT_COMBINATION", 422)
    if scenario["target_id"].startswith("mcp") and not os.getenv("MCP_URL"):
        raise store.LabError("FAULT_LAB_MCP_REQUIRED", 503)
    return scenario


def submit(request: dict, key: str, *, suite: bool = False) -> dict:
    for case in request["cases"] if suite else [request]:
        validate_request(case)
    store.initialize()
    payload, created = store.create("suite" if suite else "run", request, key)
    if created:
        POOL.submit(_execute, payload)
    return payload


def _execute(payload: dict) -> None:
    try:
        with store.lease(payload["id"]):
            cases = payload["request"].get("cases", [payload["request"]])
            payload.update(
                lifecycle="RUNNING", started_at=datetime.now(UTC).isoformat(), results=[]
            )
            store.save(payload["id"], payload)
            for index, case in enumerate(cases):
                output = journal_path(payload["id"], f"{index}.json")
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "backend.tests.support.fault_lab_runner",
                        "--id",
                        payload["id"],
                        "--index",
                        str(index),
                    ],
                    cwd=Path(__file__).resolve().parents[3],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 240
                next_heartbeat = 0
                while child.poll() is None:
                    if time.monotonic() >= next_heartbeat:
                        with store.connection_scope() as connection:
                            connection.execute(
                                text(
                                    "UPDATE fault_lab_environment SET heartbeat=now(),detail=CAST(:detail AS JSONB) WHERE id=1 AND owner=:owner"
                                ),
                                {
                                    "owner": payload["id"],
                                    "detail": json.dumps(
                                        {
                                            "runner_pid": child.pid,
                                            "api_pid": os.getpid(),
                                            "case_index": index,
                                            "timeout_seconds": 240,
                                        }
                                    ),
                                },
                            )
                        next_heartbeat = time.monotonic() + 5
                    if time.monotonic() >= deadline:
                        if os.name == "nt":
                            subprocess.run(
                                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                            )
                        else:
                            child.kill()
                        child.wait(timeout=10)
                        raise TimeoutError("Fault runner exceeded deadline")
                    time.sleep(0.2)
                if child.returncode != 0 or not output.exists():
                    # Do not expose subprocess stderr: provider/DB exceptions may contain secrets.
                    store.dirty(
                        {
                            "run_id": payload["id"],
                            "reason": "RUNNER_INTERRUPTED",
                            "case_index": index,
                        }
                    )
                    payload.update(
                        lifecycle="ABORTED", verdict="INCONCLUSIVE", error="RUNNER_INTERRUPTED"
                    )
                    break
                result = json.loads(output.read_text(encoding="utf-8"))
                payload["results"].append(result)
                store.save(payload["id"], payload)
                if not result.get("cleanup_ok"):
                    store.dirty({"run_id": payload["id"], "reason": "CLEANUP_FAILED"})
                    break
            if payload["lifecycle"] != "ABORTED":
                verdicts = [row["verdict"] for row in payload["results"]]
                payload.update(
                    lifecycle="COMPLETED",
                    verdict="FAIL"
                    if "FAIL" in verdicts
                    else "INCONCLUSIVE"
                    if "INCONCLUSIVE" in verdicts
                    else "NOT_EXECUTED"
                    if "NOT_EXECUTED" in verdicts
                    else "PASS",
                )
            payload["counts"] = {
                name: sum(r["verdict"] == name for r in payload["results"])
                for name in ["PASS", "FAIL", "NOT_EXECUTED", "INCONCLUSIVE"]
            }
            payload["counts"].update(
                selected=len(cases),
                supported=len(cases),
                rejected=0,
                executed=len(payload["results"]),
            )
            outcomes = payload["results"]
            excluded = {
                "unsupported_capability",
                "game_data_unavailable",
                "supervisor_state_conflict",
                "input_validation_error",
                "input_guard_blocked",
            }
            failure_outcomes = [
                row for row in outcomes if row.get("scenario", {}).get("fault_id") not in excluded
            ]
            injected = sum(
                any(j.get("applied") for j in row.get("injection_journal", []))
                for row in failure_outcomes
            )
            recovery_times = [
                row["detection_to_completion_seconds"]
                for row in failure_outcomes
                if row.get("recovered") and row.get("detection_to_completion_seconds") is not None
            ]

            recovered = sum(bool(row.get("recovered")) for row in outcomes)
            decidable = payload["counts"]["PASS"] + payload["counts"]["FAIL"]
            payload["metrics"] = {
                "pass_rate": payload["counts"]["PASS"] / decidable if decidable else None,
                "pass_rate_denominator": decidable,
                "injected_cases": injected,
                "recovered_cases": recovered,
                "automatic_recovery_rate": recovered / injected if injected else None,
                "safe_stop_is_recovery": False,
                "business_input_cas_cases": len(outcomes) - len(failure_outcomes),
                "recovery_timing_measured_cases": len(recovery_times),
                "mean_detection_to_completion_seconds": sum(recovery_times) / len(recovery_times)
                if recovery_times
                else None,
            }
            payload["finished_at"] = datetime.now(UTC).isoformat()
            store.save(payload["id"], payload)
    except Exception:  # noqa: BLE001 - persist worker failure without leaking connection/provider details
        if payload.get("lifecycle") == "RUNNING":
            store.dirty(
                {"run_id": payload["id"], "reason": "RUNNER_INTERRUPTED_REQUIRES_INSPECTION"}
            )
        payload.update(
            lifecycle="ABORTED", verdict="INCONCLUSIVE", error="FAULT_RUNNER_UNAVAILABLE"
        )
        store.save(payload["id"], payload)


def recover_interrupted() -> None:
    """A restart never replays unfinished work or silently steals the lease."""
    store.initialize()
    with store.connection_scope() as connection:
        pending = connection.execute(
            text(
                "SELECT id,payload FROM fault_lab_records WHERE payload->>'lifecycle' IN ('QUEUED','RUNNING')"
            )
        ).all()
    if pending:
        store.dirty({"reason": "RESTART_REQUIRES_OWNER_AND_POLICY_CHECK"})
        for row in pending:
            payload = dict(row.payload)
            payload.update(lifecycle="ABORTED", verdict="INCONCLUSIVE", error="RUNNER_RESTARTED")
            store.save(str(row.id), payload)


def cleanup_history() -> int:
    """Explicit 30-day cleanup; lease and resolved UUID paths protect live/baseline data."""
    store.initialize()
    with store.lease("history-cleanup"), store.connection_scope() as connection:
        expired = (
            connection.execute(
                text(
                    "SELECT id FROM fault_lab_records WHERE created_at < now()-interval '30 days' AND payload->>'lifecycle' IN ('COMPLETED','ABORTED')"
                )
            )
            .scalars()
            .all()
        )
        for identifier in expired:
            canonical = str(UUID(str(identifier)))
            for path in JOURNAL_ROOT.glob(f"{canonical}.*"):
                resolved = path.resolve()
                if resolved.parent != JOURNAL_ROOT.resolve() or path.is_symlink():
                    raise store.LabError("FAULT_JOURNAL_PATH_UNSAFE", 403)
                path.unlink()
            connection.execute(
                text("DELETE FROM fault_lab_records WHERE id=CAST(:id AS UUID)"),
                {"id": canonical},
            )
    return len(expired)
