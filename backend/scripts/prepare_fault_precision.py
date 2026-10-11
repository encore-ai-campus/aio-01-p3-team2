"""Read-only lab preflight and evidence export; never starts a fault run."""

import argparse
import hashlib
import json
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/ik/기획안설계/18_오류주입_시험명세.json"
OUTPUT = ROOT / "backend/test-results/fault-lab/precision"
EXCLUDED = {
    "input_validation_error",
    "input_guard_blocked",
    "unsupported_capability",
    "game_data_unavailable",
    "supervisor_state_conflict",
}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_json(client, path):
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def source_identity() -> dict:
    files = {}
    for directory, suffixes in (
        ("backend/app", {".py", ".json", ".yaml"}),
        ("backend/tests/support", {".py"}),
        ("backend/scripts", {".py"}),
        ("mcp_server", {".py"}),
        ("frontend-web/src", {".js", ".css"}),
    ):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    return {"head": head, "dirty": dirty, "source_digest": digest(files), "file_digests": files}


def preflight(client, manifest: dict) -> dict:
    environment = read_json(client, "/api/test/fault-environment")
    if environment["state"] != "CLEAN":
        raise ValueError("Lab must be CLEAN; no automatic reset or cleanup is permitted")
    catalog = read_json(client, "/api/test/fault-catalog")
    policy = next(
        row
        for row in read_json(client, "/api/v1/recovery-policies")["versions"]
        if row["status"] == "ACTIVE"
    )
    if (
        policy["version"] != manifest["active_policy_version"]
        or digest(policy["policy"]) != manifest["original_policy_content_digest"]
    ):
        raise ValueError("Active policy changed; review and version the manifest before testing")
    if catalog.get("version") != manifest["source_catalog_version"]:
        raise ValueError("Catalog version changed")
    scenarios = {row["scenario_id"]: row for row in catalog["scenarios"]}
    effective_routes = dict(policy["policy"]["routes"])
    for destination, source in policy.get("compatibility_mappings", {}).items():
        if destination not in effective_routes:
            effective_routes[destination] = effective_routes[source]
    if len(scenarios) != 28:
        raise ValueError("Expected the reviewed 28-scenario catalog")
    requests = set()
    for case in manifest["cases"]:
        request = case["request"]
        scenario = scenarios[request["scenario_id"]]
        if (
            request["pattern"] not in scenario["patterns"]
            or request["profile"] not in scenario["profiles"]
            or not 1 <= request["first_n"] <= 4
            or case["target_id"] != scenario["target_id"]
            or case["expected"]["catalog_code"] != scenario["code"]
            or case["expected"]["observed_code"] != scenario.get("observed_code", scenario["code"])
        ):
            raise ValueError(f"Catalog/manifest mismatch: {case['case_id']}")
        if digest(request) in requests:
            raise ValueError("Duplicate planned request")
        requests.add(digest(request))
        expected_rule = effective_routes[scenario.get("observed_code", scenario["code"])]
        if request["profile"] == "safe_stop":
            expected_rule = {"action": "SAFE_STOP", "max_attempts": 1}
        elif request["profile"] == "retry_allowed":
            expected_rule = {
                "action": {
                    "AGENT": "RETRY_AGENT",
                    "OPERATION": "RETRY_OPERATION",
                    "PLAN": "REVISE_PLAN",
                }[scenario["retry_class"]],
                "max_attempts": 3 if scenario["retry_class"] == "PLAN" else 4,
            }
        if case["expected"]["policy_rule"] != expected_rule:
            raise ValueError(f"Expected policy rule changed: {case['case_id']}")
    game = read_json(client, "/api/game/state")
    if game["versions"]["game_data_snapshot_id"] != "opong-data-1.5.0":
        raise ValueError("Unexpected game baseline")
    for port in (5183, 8014):
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "PREPARED_NOT_FULLY_TESTED",
        "source": source_identity(),
        "manifest_digest": digest(manifest),
        "catalog_digest": digest(catalog),
        "policy_version": policy["version"],
        "policy_content_digest": digest(policy["policy"]),
        "compatibility_mappings": policy.get("compatibility_mappings", {}),
        "game_versions": game["versions"],
        "game_state_digest": digest(game),
        "environment": environment["state"],
        "planned_cases": len(manifest["cases"]),
        "connectivity": "UI/MCP TCP reachable; real MCP operation verified separately by smoke test",
        "scope": "single fault; deterministic LLM adapter; real lab DB/MCP",
    }


def export_runs(client, manifest: dict, identifiers: list[str]) -> dict:
    cases = {digest(case["request"]): case for case in manifest["cases"]}
    rows = []
    seen_runs = set()
    for identifier in identifiers:
        run_id = str(UUID(identifier))
        if run_id in seen_runs:
            raise ValueError("Duplicate run ID would double-count evidence")
        seen_runs.add(run_id)
        record = None
        for kind in ("runs", "suites"):
            response = client.get(f"/api/test/fault-{kind}/{run_id}")
            if response.status_code == 404:
                continue
            response.raise_for_status()
            record = response.json()
            break
        if record is None or record["lifecycle"] != "COMPLETED":
            raise ValueError("Only existing completed runs can be exported")
        requests = record["request"].get("cases", [record["request"]])
        if len(requests) != len(record["results"]):
            raise ValueError("Incomplete case evidence")
        for index, (request, result) in enumerate(zip(requests, record["results"], strict=True)):
            case = cases.get(digest(request))
            if not case:
                raise ValueError("Request is outside the reviewed manifest")
            expected = case["expected"]
            checks = [
                {
                    "field": field,
                    "expected": expected[key],
                    "actual": result.get(field),
                    "passed": result.get(field) == expected[key],
                }
                for field, key in (
                    ("actual_terminal", "terminal"),
                    ("attempts", "target_attempts"),
                    ("fallback_calls", "fallback_calls"),
                    ("observed_error_code", "observed_code"),
                    ("applied_policy_rule", "policy_rule"),
                )
            ]
            actual_actions = next(
                (
                    c["actual"]
                    for c in result.get("checks", [])
                    if c["name"] == "policy_recovery_actions"
                ),
                [],
            )
            checks.append(
                {
                    "field": "policy_actions",
                    "expected": expected["policy_actions"],
                    "actual": actual_actions,
                    "passed": actual_actions == expected["policy_actions"],
                }
            )
            if expected["finding"]:
                present = any(
                    f.get("repairability") == expected["finding"] and f.get("blocks_runtime")
                    for f in result.get("business_findings", [])
                )
                checks.append(
                    {"field": "finding", "expected": True, "actual": present, "passed": present}
                )
            verdict = result["verdict"]
            if verdict == "PASS" and not all(c["passed"] for c in checks):
                verdict = "FAIL"
            rows.append(
                {
                    "case_id": case["case_id"],
                    "run_id": run_id,
                    "case_index": index,
                    "request": request,
                    "expected": expected,
                    "manifest_checks": checks,
                    "stored_verdict": result["verdict"],
                    "report_verdict": verdict,
                    "actual": result,
                    "ui_review": "NOT_REVIEWED",
                    "original_input_fixture_digest": result.get("fixture_digest"),
                }
            )
    eligible = [row for row in rows if row["request"]["scenario_id"] not in EXCLUDED]
    recovered = [
        row
        for row in eligible
        if row["report_verdict"] == "PASS" and row["actual"].get("recovered")
    ]
    times = [
        row["actual"]["detection_to_completion_seconds"]
        for row in recovered
        if row["actual"].get("detection_to_completion_seconds") is not None
    ]
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_digest": digest(manifest),
        "rows": rows,
        "counts": {
            v: sum(row["report_verdict"] == v for row in rows)
            for v in ("PASS", "FAIL", "INCONCLUSIVE", "NOT_EXECUTED")
        },
        "metrics": {
            "selected_error_cases": len(eligible),
            "automatic_normalizations": len(recovered),
            "automatic_normalization_rate": len(recovered) / len(eligible) if eligible else None,
            "excluded_scenarios": sorted(EXCLUDED),
            "timed_successes": len(times),
            "successful_time_missing": len(recovered) - len(times),
            "mean_recovery_seconds": sum(times) / len(times) if times else None,
        },
        "note": "Subset evidence only; UI review and the full 137-case campaign are separate. Historical records unchanged.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", action="append", default=[])
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with httpx.Client(base_url="http://127.0.0.1:8013", timeout=15, trust_env=False) as client:
        baseline = preflight(client, manifest)
        report = export_runs(client, manifest, args.run_id) if args.run_id else None
    OUTPUT.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid4())
    path = OUTPUT / f"baseline-{identifier}.json"
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Preflight: {path}")
    if report is not None:
        path = OUTPUT / f"evidence-{identifier}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Evidence: {path}")


if __name__ == "__main__":
    main()
