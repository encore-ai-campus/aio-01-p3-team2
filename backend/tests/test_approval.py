from uuid import uuid4

from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import TaskRecord, WorkflowStatus
from backend.app.services.db_service import repository
from backend.app.services.workflow_service import now


def create_task(client, actor_id="liveops01"):
    response = client.post(
        "/api/events/generate",
        json={
            "actor_id": actor_id,
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


def test_approved_actor_ids_come_from_environment(client, monkeypatch):
    monkeypatch.setenv("APPROVED_ACTOR_IDS", "operator02,operator03")
    monkeypatch.setenv("OPERATOR_ACTOR_ID", "operator02")
    task = create_task(client, actor_id="operator02")
    denied = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={"actor_id": "liveops01", "result_version": 1, "action": "APPROVE"},
    )
    assert denied.status_code == 403

    forged_allowlisted_actor = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={"actor_id": "operator03", "result_version": 1, "action": "APPROVE"},
    )
    assert forged_allowlisted_actor.status_code == 403

    allowed = client.post(
        f"/api/tasks/{task['task_id']}/approval",
        json={"actor_id": "operator02", "result_version": 1, "action": "APPROVE"},
    )
    assert allowed.status_code == 200
    traces = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"]
    assert next(item for item in traces if item["action"] == "APPROVE")["actor_id"] == "operator02"


def test_generation_rejects_actor_id_spoofing(client):
    response = client.post(
        "/api/events/generate",
        json={
            "actor_id": "operator02",
            "event_name": "위조 ID 시험",
            "target_user": "all_user",
            "duration": 2,
            "objective": "서버 운영자 ID 대조",
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "INVALID_ACTOR"


def test_delete_task_removes_saved_result_and_related_api_records(client):
    task = create_task(client)
    task_id = task["task_id"]
    path = f"/api/tasks/{task_id}"

    denied = client.request("DELETE", path, json={"actor_id": "unknown"})
    assert denied.status_code == 403
    assert client.get(path).status_code == 200

    deleted = client.request("DELETE", path, json={"actor_id": "liveops01"})
    assert deleted.status_code == 200
    assert deleted.json() == {"task_id": task_id, "deleted": True}
    assert client.get(path).status_code == 404
    assert client.get(f"{path}/trace").status_code == 404
    assert client.get(f"{path}/tool-logs").status_code == 404
    assert task_id not in [item["task_id"] for item in client.get("/api/tasks").json()["tasks"]]
    assert client.request("DELETE", path, json={"actor_id": "liveops01"}).status_code == 404


def test_delete_rejects_running_task(client):
    timestamp = now()
    task = TaskRecord(
        task_id="TASK-RUNNING123",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="진행 중 이벤트",
            target_user="new_user",
            duration=1,
            objective="진행 중 삭제 방지",
        ),
        workflow_status=WorkflowStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )
    repository.create_task(task)

    response = client.request(
        "DELETE", f"/api/tasks/{task.task_id}", json={"actor_id": "liveops01"}
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "TASK_IN_PROGRESS"
    assert repository.get_task(task.task_id) == task
