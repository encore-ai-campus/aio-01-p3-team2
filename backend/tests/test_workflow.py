from uuid import uuid4


def payload(**overrides):
    value = {
        "actor_id": "liveops01",
        "event_name": "신규 유저 성장 이벤트",
        "target_user": "new_user",
        "duration": 3,
        "objective": "초기 적응 지원",
        "idempotency_key": str(uuid4()),
    }
    value.update(overrides)
    return value


def test_generate_lookup_and_trace(client):
    response = client.post("/api/events/generate", json=payload())
    assert response.status_code == 201
    task = response.json()
    assert task["workflow_status"] == "WAITING_APPROVAL"
    assert task["approval_status"] == "PENDING"
    assert task["result_version"] == 1
    assert task["final_result"] is None
    assert len(task["current_result"]["missions"]) == 3

    task_id = task["task_id"]
    assert client.get(f"/api/tasks/{task_id}").json() == task
    traces = client.get(f"/api/tasks/{task_id}/trace").json()["traces"]
    assert [item["sequence"] for item in traces] == [1, 2]


def test_idempotency_reuses_same_task(client):
    request = payload()
    first = client.post("/api/events/generate", json=request)
    second = client.post("/api/events/generate", json=request)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]


def test_idempotency_conflict(client):
    request = payload()
    client.post("/api/events/generate", json=request)
    request["objective"] = "다른 목적"
    response = client.post("/api/events/generate", json=request)
    assert response.status_code == 409
    assert response.json()["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_validation_uses_common_error_contract(client):
    response = client.post("/api/events/generate", json=payload(duration=8))
    assert response.status_code == 422
    assert response.json()["error_code"] == "INPUT_VALIDATION_ERROR"
