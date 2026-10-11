"""Focused HTTP integration check for an already running isolated lab."""

import argparse
import json
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from backend.tests.support.fault_lab_runner import brief_fixture


def verify(base_url: str) -> dict:
    target = urlsplit(base_url)
    if target.hostname != "127.0.0.1" or target.port != 8013:
        raise ValueError("Only the standard local isolated lab endpoint is allowed")
    results = {}
    with httpx.Client(base_url=base_url, timeout=30) as client:
        catalog = client.get("/api/test/fault-catalog")
        catalog.raise_for_status()
        results["catalog_codes"] = len({row["fault_id"] for row in catalog.json()["scenarios"]})
        assert results["catalog_codes"] == 28
        invalid = client.post(
            "/api/test/fault-runs",
            json={"scenario_id": "llm_timeout", "database_url": "production"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert invalid.status_code == 422
        forbidden = client.post(
            "/api/test/fault-runs",
            json={"scenario_id": "handoff_contract_invalid", "profile": "retry_allowed"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert forbidden.status_code == 422
        origin = client.post(
            "/api/test/fault-runs",
            json={"scenario_id": "unknown_error"},
            headers={"Idempotency-Key": str(uuid4()), "Origin": "https://untrusted.invalid"},
        )
        assert origin.status_code == 403
        # During another injected case, this ordinary request must execute without injection.
        environment = client.get("/api/test/fault-environment").json()
        response = client.post(
            "/api/v1/event-briefs", json=brief_fixture(), headers={"Idempotency-Key": str(uuid4())}
        )
        response.raise_for_status()
        workflow = response.json()
        assert workflow["workflow_status"] == "WAITING_APPROVAL"
        assert "fault-lab" not in json.dumps(workflow)
        results.update(
            environment_at_normal_request=environment["state"],
            normal_workflow_id=workflow["workflow_id"],
            normal_request_unaffected=True,
            invalid_environment_rejected=True,
            forbidden_retry_rejected=True,
            foreign_origin_rejected=True,
        )
        # Active profile changes during a suite are expected; wait for cleanup before checking.
        deadline = time.monotonic() + 240
        while client.get("/api/test/fault-environment").json()["state"] == "BUSY":
            if time.monotonic() > deadline:
                raise TimeoutError("Lab did not finish within its case deadline")
            time.sleep(1)
        assert client.get("/api/test/fault-environment").json()["state"] == "CLEAN"
        results["environment_clean_after_suite"] = True
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8013")
    args = parser.parse_args()
    print(json.dumps(verify(args.base_url), ensure_ascii=False, indent=2))
