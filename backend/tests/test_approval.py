from uuid import uuid4


def create_task(client):
    response = client.post(
        "/api/events/generate",
        json={
            "actor_id": "liveops01",
            "event_name": "승인 테스트",
            "target_user": "all_user",
            "duration": 2,
            "objective": "승인 흐름 확인",
            "idempotency_key": str(uuid4()),
        },
    )
    return response.json()


def test_approve(client):
    task = create_task(client)
    response = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={"actor_id": "liveops01", "result_version": 1, "action": "APPROVE"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["workflow_status"] == "COMPLETED"
    assert result["approval_status"] == "APPROVED"
    assert result["final_result"] == result["current_result"]


def test_request_revision_increments_version_not_revision_count(client):
    task = create_task(client)
    response = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={
            "actor_id": "liveops01",
            "result_version": 1,
            "action": "REQUEST_REVISION",
            "approval_comment": "초반 미션을 더 쉽게 수정해주세요.",
        },
    )
    result = response.json()
    assert result["result_version"] == 2
    assert result["revision_count"] == 0
    assert result["workflow_status"] == "WAITING_APPROVAL"


def test_stale_version_is_rejected(client):
    task = create_task(client)
    response = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={"actor_id": "liveops01", "result_version": 99, "action": "APPROVE"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "STALE_RESULT_VERSION"


def test_duplicate_approval_returns_original_result(client):
    task = create_task(client)
    request = {"actor_id": "liveops01", "result_version": 1, "action": "APPROVE"}
    first = client.post(f"/api/tasks/{task['task_id']}/approval", json=request)
    second = client.post(f"/api/tasks/{task['task_id']}/approval", json=request)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    traces = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"]
    approval_traces = [item for item in traces if item["action"] == "APPROVE"]
    assert len(approval_traces) == 1
