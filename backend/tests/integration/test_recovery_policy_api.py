import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def test_policy_lifecycle_persists_version_and_audit(client):
    initial = client.get("/api/v1/recovery-policies")
    assert initial.status_code == 200
    active = next(row for row in initial.json()["versions"] if row["status"] == "ACTIVE")
    draft = active["policy"]
    draft["routes"]["ERR-DEP-MCP-001"]["max_attempts"] = 2
    key = str(uuid4())

    created = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": key},
        json=draft,
    )
    replay = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": key},
        json=draft,
    )
    assert created.status_code == 201
    assert replay.json()["version"] == created.json()["version"]
    version = created.json()["version"]

    assert client.post(f"/api/v1/recovery-policies/{version}/validate").json()["valid"]
    preview = client.post(f"/api/v1/recovery-policies/{version}/preview").json()
    assert preview["valid"] is True
    assert preview["runtime_mutated"] is False
    assert client.post(f"/api/v1/recovery-policies/{version}/activate").status_code == 200
    assert client.post(f"/api/v1/recovery-policies/{active['version']}/rollback").status_code == 200

    final = client.get("/api/v1/recovery-policies").json()
    assert any(row["action"] == "ROLLBACK" for row in final["audit"])
