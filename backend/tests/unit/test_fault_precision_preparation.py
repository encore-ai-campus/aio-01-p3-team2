"""Preflight/export negative controls; no real network, DB, or server mutations."""

from contextlib import nullcontext

import httpx
import pytest
from backend.scripts import prepare_fault_precision as prep


def setup_preflight(monkeypatch):
    code = "ERR-DEP-LLM-001"
    policy = {"routes": {code: {"action": "RETRY_AGENT", "max_attempts": 4}}}
    scenario = {
        "scenario_id": "llm_timeout",
        "code": code,
        "target_id": "planner.call",
        "retry_class": "AGENT",
        "patterns": ["once"],
        "profiles": ["current_active"],
    }
    request = {
        "scenario_id": "llm_timeout",
        "pattern": "once",
        "first_n": 1,
        "profile": "current_active",
    }
    case = {
        "case_id": "case",
        "target_id": "planner.call",
        "request": request,
        "expected": {
            "catalog_code": code,
            "observed_code": code,
            "policy_rule": policy["routes"][code],
        },
    }
    manifest = {
        "active_policy_version": 1,
        "original_policy_content_digest": prep.digest(policy),
        "source_catalog_version": "v1",
        "cases": [case],
    }
    catalog = {
        "version": "v1",
        "scenarios": [scenario, *[{**scenario, "scenario_id": f"other-{i}"} for i in range(27)]],
    }
    data = {
        "/api/test/fault-environment": {"state": "CLEAN"},
        "/api/test/fault-catalog": catalog,
        "/api/v1/recovery-policies": {
            "versions": [{"version": 1, "status": "ACTIVE", "policy": policy}]
        },
        "/api/game/state": {"versions": {"game_data_snapshot_id": "opong-data-1.5.0"}},
    }
    client = httpx.Client(
        base_url="http://127.0.0.1:8013",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=data[request.url.path])
        ),
    )
    monkeypatch.setattr(prep.socket, "create_connection", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(prep, "source_identity", lambda: {"head": "test", "dirty": True})
    return client, manifest, data


def test_preflight_accepts_frozen_baseline(monkeypatch):
    client, manifest, _ = setup_preflight(monkeypatch)
    with client:
        assert prep.preflight(client, manifest)["planned_cases"] == 1


@pytest.mark.parametrize("mutation", ["busy", "policy", "rule", "snapshot"])
def test_preflight_rejects_changed_environment_or_expectation(monkeypatch, mutation):
    client, manifest, data = setup_preflight(monkeypatch)
    if mutation == "busy":
        data["/api/test/fault-environment"]["state"] = "BUSY"
    elif mutation == "policy":
        data["/api/v1/recovery-policies"]["versions"][0]["version"] = 2
    elif mutation == "rule":
        manifest["cases"][0]["expected"]["policy_rule"] = {"action": "SAFE_STOP", "max_attempts": 1}
    else:
        data["/api/game/state"]["versions"]["game_data_snapshot_id"] = "wrong"
    with client, pytest.raises(ValueError):
        prep.preflight(client, manifest)
