"""Shared reservations survive a competing process and an interrupted owner."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="isolated PostgreSQL profile required"
)


def test_legacy_task_dead_owner_stops_and_late_result_is_fenced(tmp_path, client):
    from backend.app.services import task_service
    from backend.app.services.db_service import repository

    receipt = tmp_path / "task-owner.json"
    code = """
import json,sys,time
from pathlib import Path
from uuid import uuid4
from backend.app.schemas.event import EventCreateRequest
from backend.app.services import task_service
def interrupted(task_id,brief,fault_type=None):
    Path(sys.argv[1]).write_text(json.dumps({'task_id':task_id}),encoding='utf-8')
    time.sleep(60)
task_service.workflow_engine.run=interrupted
task_service.generate(EventCreateRequest(event_name='interrupted task',target_user='all_user',duration=1,objective='deadline test',idempotency_key=uuid4()))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(receipt)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(500):
            if receipt.exists():
                break
            assert process.poll() is None, "task process exited before execution"
            time.sleep(0.01)
        assert receipt.exists()
        task_id = json.loads(receipt.read_text(encoding="utf-8"))["task_id"]
        stale = repository.get_task(task_id)
        assert stale.execution_owner and stale.execution_deadline
        process.kill()
        process.wait(timeout=5)
        # Move the persisted deadline forward in the test clock, without re-executing work.
        with repository.atomic():
            interrupted_task = repository.get_task(task_id)
            interrupted_task.execution_deadline = datetime.now(UTC) - timedelta(seconds=1)
            repository.save_task(interrupted_task)
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["workflow_status"] == "FAILED"
        assert response.json()["error"]["error_type"] == "workflow_deadline_exceeded"
        assert "execution_owner" not in response.json()
        assert repository.get_traces(task_id)[-1].workflow_state == "SAFE_STOP"
        assert task_service.reconcile_expired_tasks() == 0
        with repository.atomic():
            winner = task_service._execution_winner(stale)
        assert winner.workflow_status == "FAILED"
        assert repository.get_task(task_id).execution_owner is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_other_process_reuses_reservation_and_dead_owner_stops(tmp_path, client):
    from backend.app.services import workflow_execution
    from backend.tests.unit.test_planning_api import _brief

    receipt = tmp_path / "receipt.json"
    calls = tmp_path / "calls.txt"
    key = str(uuid4())
    brief = _brief()
    code = """
import asyncio,json,sys
from pathlib import Path
from backend.app.agents.canonical_planner import CanonicalPlanner
from backend.app.schemas.planning import EventBriefInput
from backend.app.services.recovery_policy import pin_active_policy
from backend.app.services.workflow_execution import submit
async def slow(self, context):
    Path(sys.argv[3]).write_text('one planner call',encoding='utf-8')
    await asyncio.sleep(30)
CanonicalPlanner.plan=slow
with pin_active_policy():
    result=submit(EventBriefInput.model_validate(json.loads(sys.argv[4])),sys.argv[1],asynchronous=True)
Path(sys.argv[2]).write_text(json.dumps(result),encoding='utf-8')
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, key, str(receipt), str(calls), json.dumps(brief)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(500):
            if receipt.exists() and calls.exists():
                break
            assert process.poll() is None, "owner process exited before reserving execution"
            time.sleep(0.01)
        assert receipt.exists() and calls.exists()
        admitted = json.loads(receipt.read_text(encoding="utf-8"))
        duplicate = client.post(
            "/api/v1/event-briefs",
            json=brief,
            headers={"Idempotency-Key": key, "Prefer": "respond-async"},
        )
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["workflow_id"] == admitted["workflow_id"]
        assert duplicate.json()["current_status"] == "PLANNING"
    finally:
        process.kill()
        process.wait(timeout=5)

    with Session(workflow_execution._engine()) as session, session.begin():
        row = session.scalar(
            select(workflow_execution.WorkflowOperation).where(
                workflow_execution.WorkflowOperation.idempotency_key == key
            )
        )
        row.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    workflow_execution.reconcile_expired()
    restored = client.get("/api/v1/workflows/" + admitted["workflow_id"]).json()
    assert restored["current_status"] == "SAFE_STOP"
    assert restored["error"]["code"] == "ERR-CTRL-DEADLINE-001"
    assert "plan" not in restored
    assert calls.read_text(encoding="utf-8") == "one planner call"
    assert sum(row.get("workflow_event") == "safe_stop" for row in restored["trace"]) == 1
