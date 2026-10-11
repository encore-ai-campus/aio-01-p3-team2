import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def test_policy_lifecycle_persists_version_and_audit(client):
    initial = client.get("/api/v1/recovery-policies")
    assert initial.status_code == 200
    assert initial.json()["error_catalog"]["ERR-DEP-MCP-001"] == {
        "category": "DEPENDENCY",
        "location": "MCP",
        "retry_class": "OPERATION",
        "safe_message": "게임 데이터 조회 시간이 초과되었습니다.",
    }
    active = next(row for row in initial.json()["versions"] if row["status"] == "ACTIVE")
    draft = active["policy"]
    assert draft["routes"]["ERR-CTRL-BUDGET-001"] == {
        "action": "SAFE_STOP",
        "max_attempts": 1,
    }
    assert draft["routes"]["ERR-CTRL-DEADLINE-001"] == {
        "action": "SAFE_STOP",
        "max_attempts": 1,
    }
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
    activate_key = str(uuid4())
    activated = client.post(
        f"/api/v1/recovery-policies/{version}/activate",
        headers={"Idempotency-Key": activate_key},
    )
    replayed = client.post(
        f"/api/v1/recovery-policies/{version}/activate",
        headers={"Idempotency-Key": activate_key},
    )
    assert activated.status_code == 200, activated.text
    assert replayed.json() == activated.json()
    rollback = client.post(
        f"/api/v1/recovery-policies/{active['version']}/rollback",
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert rollback.status_code == 200, rollback.text

    final = client.get("/api/v1/recovery-policies").json()
    assert any(row["action"] == "ROLLBACK" for row in final["audit"])
    assert final["audit"][0]["actor"] == "liveops01"


def test_workflow_keeps_pinned_policy_version_after_activation(client):
    policy_state = client.get("/api/v1/recovery-policies").json()
    original = next(row for row in policy_state["versions"] if row["status"] == "ACTIVE")
    original_version = original["version"]
    pinned_run = _create_workflow(client)
    assert pinned_run["recovery_policy_version"] == original_version

    draft = original["policy"]
    retry_route = draft["routes"]["ERR-DEP-MCP-001"]
    retry_route["max_attempts"] = 1 if retry_route["max_attempts"] != 1 else 2
    created = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": str(uuid4())},
        json=draft,
    )
    assert created.status_code == 201, created.text
    new_version = created.json()["version"]
    assert client.post(f"/api/v1/recovery-policies/{new_version}/validate").json()["valid"]
    activation_key = str(uuid4())
    activation = client.post(
        f"/api/v1/recovery-policies/{new_version}/activate",
        headers={"Idempotency-Key": activation_key},
    )
    assert activation.status_code == 200, activation.text
    try:
        stored_old_run = client.get(f"/api/v1/workflows/{pinned_run['workflow_id']}").json()
        next_run = _create_workflow(client)
        assert stored_old_run["recovery_policy_version"] == original_version
        assert next_run["recovery_policy_version"] == new_version
    finally:
        rollback = client.post(
            f"/api/v1/recovery-policies/{original_version}/rollback",
            headers={"Idempotency-Key": str(uuid4())},
        )
    assert rollback.status_code == 200, rollback.text


def test_policy_activation_retry_recovers_committed_result(client, monkeypatch):
    from backend.app.services import recovery_policy

    initial = client.get("/api/v1/recovery-policies").json()
    active = next(row for row in initial["versions"] if row["status"] == "ACTIVE")
    policy = active["policy"]
    attempts = policy["routes"]["ERR-DEP-MCP-001"]["max_attempts"]
    policy["routes"]["ERR-DEP-MCP-001"]["max_attempts"] = 1 if attempts != 1 else 2
    created = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": str(uuid4())},
        json=policy,
    )
    assert created.status_code == 201, created.text
    version = created.json()["version"]
    assert client.post(f"/api/v1/recovery-policies/{version}/validate").json()["valid"]

    class CommitAckLossConnection:
        def __init__(self, engine, lose_ack):
            self.engine = engine
            self.lose_ack = lose_ack

        def begin(self):
            return CommitAckLossTransaction(self, self.engine.begin())

        def connect(self):
            return self.engine.connect()

    class CommitAckLossTransaction:
        def __init__(self, proxy, transaction):
            self.proxy = proxy
            self.transaction = transaction
            self.connection = None
            self.wrote_operation = False

        def __enter__(self):
            self.connection = self.transaction.__enter__()
            return self

        def execute(self, statement, parameters=None, *args, **kwargs):
            if "INSERT INTO recovery_policy_operations" in str(statement):
                self.wrote_operation = True
            return self.connection.execute(statement, parameters, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.transaction.__exit__(exc_type, exc_value, traceback)
            if self.wrote_operation and self.proxy.lose_ack:
                self.proxy.lose_ack = False
                raise OperationalError("COMMIT", {}, OSError("injected lost acknowledgement"))
            return result

    proxy = CommitAckLossConnection(recovery_policy._engine(), lose_ack=True)
    monkeypatch.setattr(recovery_policy, "_engine", lambda: proxy)
    key = str(uuid4())
    path = f"/api/v1/recovery-policies/{version}/activate"
    recovered = client.post(path, headers={"Idempotency-Key": key})
    assert recovered.status_code == 200, recovered.text
    assert proxy.lose_ack is False
    replay = client.post(path, headers={"Idempotency-Key": key})
    assert replay.status_code == 200, replay.text
    assert replay.json() == recovered.json()

    conflicting = client.post(
        f"/api/v1/recovery-policies/{active['version']}/activate",
        headers={"Idempotency-Key": key},
    )
    assert conflicting.status_code == 409
    state = client.get("/api/v1/recovery-policies").json()
    assert sum(row["status"] == "ACTIVE" for row in state["versions"]) == 1
    assert (
        sum(row["version"] == version and row["action"] == "ACTIVATE" for row in state["audit"])
        == 1
    )
    assert (
        client.post(
            f"/api/v1/recovery-policies/{active['version']}/rollback",
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 200
    )


def test_concurrent_policy_activations_leave_exactly_one_active_version(client):
    from backend.app.services.recovery_policy import activate_version, list_policies

    original = next(
        row
        for row in client.get("/api/v1/recovery-policies").json()["versions"]
        if row["status"] == "ACTIVE"
    )
    versions = []
    for attempts in (1, 2):
        policy = original["policy"]
        policy["routes"]["ERR-DEP-MCP-001"]["max_attempts"] = attempts
        created = client.post(
            "/api/v1/recovery-policies/drafts",
            headers={"Idempotency-Key": str(uuid4())},
            json=policy,
        )
        assert created.status_code == 201, created.text
        version = created.json()["version"]
        assert client.post(f"/api/v1/recovery-policies/{version}/validate").json()["valid"]
        versions.append(version)

    barrier = Barrier(len(versions))

    def activate(version):
        barrier.wait(timeout=5)
        return activate_version(version, idempotency_key=str(uuid4()))

    try:
        with ThreadPoolExecutor(max_workers=len(versions)) as pool:
            results = list(pool.map(activate, versions))
        assert {result["version"] for result in results} == set(versions)
        active = [row for row in list_policies()["versions"] if row["status"] == "ACTIVE"]
        assert [row["version"] for row in active] in ([versions[0]], [versions[1]])
    finally:
        current = list_policies()
        original_row = next(
            row for row in current["versions"] if row["version"] == original["version"]
        )
        if original_row["status"] == "RETIRED":
            activate_version(original["version"], idempotency_key=str(uuid4()), rollback=True)


def _create_workflow(client):
    action_id = str(uuid4())
    reward_id = str(uuid4())
    response = client.post(
        "/api/v1/event-briefs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "objective": "활성 정책 버전 고정을 검증한다",
            "target_segments": ["new_user"],
            "event_type": "growth_progression",
            "operation_mode": "finite",
            "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
            "actions": [
                {
                    "action_id": action_id,
                    "action_type": "stage_clear",
                    "content_scope": {"content_ids": ["stage_01"]},
                    "goal": {
                        "metric": "count",
                        "aggregation": "count",
                        "operator": "gte",
                        "target": 3,
                    },
                }
            ],
            "rewards": [{"reward_id": reward_id, "item_id": "GOLD", "quantity": 20}],
            "mission_reward_links": [{"action_id": action_id, "reward_id": reward_id}],
            "impact_targets": [
                {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
            ],
            "source_game_version": "opong-data-1.3.0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
