"""Runtime writes remain recoverable when the follow-up lifecycle trace fails."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def _brief() -> dict[str, object]:
    action_id = str(uuid4())
    reward_id = str(uuid4())
    return {
        "objective": "승인 이벤트의 Trace 복구를 확인한다",
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
    }


def test_runtime_apply_retry_recovers_trace_after_first_trace_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.main import app
    from backend.app.services import canonical_planning

    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    client = TestClient(app, raise_server_exceptions=False)

    created = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    decision = client.post(
        "/api/v1/decisions",
        json={
            "workflow_id": workflow["workflow_id"],
            "plan_id": workflow["plan"]["plan_id"],
            "result_version": workflow["plan"]["result_version"],
            "action": "APPROVE",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert decision.status_code == 200, decision.text

    original_append = canonical_planning.append_workflow_lifecycle_event
    failures_remaining = 2

    def fail_runtime_trace_once(
        workflow_id: str,
        event_type: str,
        status: str,
        attributes: dict[str, object],
        dedupe_key: str,
    ) -> dict[str, object]:
        nonlocal failures_remaining
        if event_type == "runtime.event_registered" and failures_remaining:
            failures_remaining -= 1
            raise RuntimeError("injected lifecycle trace write failure")
        return original_append(workflow_id, event_type, status, attributes, dedupe_key)

    monkeypatch.setattr(
        canonical_planning, "append_workflow_lifecycle_event", fail_runtime_trace_once
    )
    apply_request = {
        "request_id": str(uuid4()),
        "task_id": workflow["task_id"],
        "result_version": workflow["plan"]["result_version"],
    }
    failed = client.post("/api/game/events/apply", json=apply_request)
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "ERR-OBS-TRACE-001"
    assert failed.json()["detail"]["operation_status"] == "ROLLED_BACK"

    still_degraded = client.post("/api/game/events/apply", json=apply_request)
    assert still_degraded.status_code == 503
    assert still_degraded.json()["detail"]["operation_status"] == "ROLLED_BACK"
    assert failures_remaining == 0

    degraded_workflow = client.get(f"/api/v1/workflows/{workflow['workflow_id']}")
    assert degraded_workflow.status_code == 200
    assert degraded_workflow.json()["current_status"] == "APPROVED"
    assert degraded_workflow.json()["runtime_event_trace_status"] is None
    assert degraded_workflow.json()["runtime_event"] is None

    recovered = client.post("/api/game/events/apply", json=apply_request)
    assert recovered.status_code == 200, recovered.text
    runtime_event_id = recovered.json()["runtime_event_id"]

    persisted = client.get(f"/api/v1/workflows/{workflow['workflow_id']}")
    assert persisted.status_code == 200
    assert persisted.json()["runtime_event_trace_status"] == "RECORDED"
    trace_events = [row["event"] for row in persisted.json()["trace"]]
    assert trace_events.count("runtime.event_registered") == 1
    assert persisted.json()["runtime_event"]["runtime_event_id"] == runtime_event_id

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            event_count = connection.execute(
                text(
                    "SELECT count(*) FROM game_runtime_events "
                    "WHERE source_task_id = :task_id AND result_version = :version"
                ),
                {"task_id": workflow["task_id"], "version": 1},
            ).scalar_one()
            request_count = connection.execute(
                text(
                    "SELECT count(*) FROM game_request_results "
                    "WHERE scope = 'event_apply' AND request_id = :request_id"
                ),
                {"request_id": apply_request["request_id"]},
            ).scalar_one()
        assert event_count == 1
        assert request_count == 1
    finally:
        engine.dispose()


def test_runtime_apply_retry_resolves_commit_acknowledgement_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost commit acknowledgement is resolved by looking up the request ID."""
    from backend.app.main import app

    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    client = TestClient(app, raise_server_exceptions=False)

    created = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    decision = client.post(
        "/api/v1/decisions",
        json={
            "workflow_id": workflow["workflow_id"],
            "plan_id": workflow["plan"]["plan_id"],
            "result_version": workflow["plan"]["result_version"],
            "action": "APPROVE",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert decision.status_code == 200, decision.text

    apply_request = {
        "request_id": str(uuid4()),
        "task_id": workflow["task_id"],
        "result_version": workflow["plan"]["result_version"],
    }
    marked_sessions: set[int] = set()

    def mark_apply_commit(session: Session) -> None:
        if any(getattr(row, "scope", None) == "event_apply" for row in session.new):
            marked_sessions.add(id(session))

    def lose_commit_acknowledgement(session: Session) -> None:
        if id(session) in marked_sessions:
            marked_sessions.remove(id(session))
            raise OperationalError("COMMIT", {}, OSError("injected lost acknowledgement"))

    event.listen(Session, "before_commit", mark_apply_commit)
    event.listen(Session, "after_commit", lose_commit_acknowledgement)
    try:
        first = client.post("/api/game/events/apply", json=apply_request)
        assert first.status_code == 200, first.text

        replay = client.post("/api/game/events/apply", json=apply_request)
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is False

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                event_count = connection.execute(
                    text(
                        "SELECT count(*) FROM game_runtime_events "
                        "WHERE source_task_id = :task_id AND result_version = :version"
                    ),
                    {"task_id": workflow["task_id"], "version": 1},
                ).scalar_one()
                request_count = connection.execute(
                    text(
                        "SELECT count(*) FROM game_request_results "
                        "WHERE scope = 'event_apply' AND request_id = :request_id"
                    ),
                    {"request_id": apply_request["request_id"]},
                ).scalar_one()
            assert event_count == 1
            assert request_count == 1
        finally:
            engine.dispose()
    finally:
        event.remove(Session, "before_commit", mark_apply_commit)
        event.remove(Session, "after_commit", lose_commit_acknowledgement)


def test_event_brief_resolves_commit_acknowledgement_loss_without_replanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.main import app
    from backend.app.services import canonical_planning

    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    client = TestClient(app, raise_server_exceptions=False)
    key = str(uuid4())
    marked_sessions: set[int] = set()

    def mark_workflow_commit(session: Session) -> None:
        if any(isinstance(row, canonical_planning._PlanningRunRow) for row in session.new):
            marked_sessions.add(id(session))

    def lose_commit_acknowledgement(session: Session) -> None:
        if id(session) in marked_sessions:
            marked_sessions.remove(id(session))
            raise OperationalError("COMMIT", {}, OSError("injected lost acknowledgement"))

    event.listen(Session, "before_commit", mark_workflow_commit)
    event.listen(Session, "after_commit", lose_commit_acknowledgement)
    try:
        response = client.post(
            "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": key}
        )
    finally:
        event.remove(Session, "before_commit", mark_workflow_commit)
        event.remove(Session, "after_commit", lose_commit_acknowledgement)

    assert response.status_code == 201, response.text
    workflow = response.json()
    persisted = client.get(f"/api/v1/workflows/{workflow['workflow_id']}")
    assert persisted.status_code == 200
    assert persisted.json()["workflow_id"] == workflow["workflow_id"]

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row_count = connection.execute(
                text("SELECT count(*) FROM canonical_planning_runs WHERE idempotency_key=:key"),
                {"key": key},
            ).scalar_one()
        assert row_count == 1
    finally:
        engine.dispose()


def test_plan_decision_resolves_commit_acknowledgement_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.main import app
    from backend.app.services import canonical_planning

    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    plan = workflow["plan"]
    key = str(uuid4())
    marked_sessions: set[int] = set()

    def mark_decision_commit(session: Session) -> None:
        if any(isinstance(row, canonical_planning._PlanningDecisionRow) for row in session.new):
            marked_sessions.add(id(session))

    def lose_commit_acknowledgement(session: Session) -> None:
        if id(session) in marked_sessions:
            marked_sessions.remove(id(session))
            raise OperationalError("COMMIT", {}, OSError("injected lost acknowledgement"))

    event.listen(Session, "before_commit", mark_decision_commit)
    event.listen(Session, "after_commit", lose_commit_acknowledgement)
    try:
        response = client.post(
            "/api/v1/decisions",
            json={
                "workflow_id": workflow["workflow_id"],
                "plan_id": plan["plan_id"],
                "result_version": plan["result_version"],
                "action": "APPROVE",
            },
            headers={"Idempotency-Key": key},
        )
    finally:
        event.remove(Session, "before_commit", mark_decision_commit)
        event.remove(Session, "after_commit", lose_commit_acknowledgement)

    assert response.status_code == 200, response.text
    assert response.json()["approval_status"] == "APPROVED"
    persisted = client.get(f"/api/v1/workflows/{workflow['workflow_id']}")
    assert persisted.status_code == 200
    assert (
        sum(row["event"] == "approval.decision_recorded" for row in persisted.json()["trace"]) == 1
    )

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row_count = connection.execute(
                text(
                    "SELECT count(*) FROM canonical_planning_decisions WHERE idempotency_key=:key"
                ),
                {"key": key},
            ).scalar_one()
        assert row_count == 1
    finally:
        engine.dispose()
