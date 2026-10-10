"""The Backend B adapter turns the global deadline into a persisted safe stop."""

import asyncio

from backend.app.schemas.event import EventBrief, TargetUser
from backend.app.services import workflow_service
from backend.app.services.workflow_service import BackendBWorkflowAdapter


def test_backend_b_deadline_returns_failure_and_writes_trace(monkeypatch) -> None:
    trace_events: list[dict[str, object]] = []

    class SlowEngine:
        async def run(self, task_id, request, fault_type):
            await asyncio.sleep(0.05)

    async def save_trace(event: dict[str, object]) -> None:
        trace_events.append(event)

    adapter = object.__new__(BackendBWorkflowAdapter)
    adapter._engine = SlowEngine()
    adapter._save_trace = save_trace
    monkeypatch.setattr(workflow_service, "WORKFLOW_DEADLINE_SECONDS", 0.001)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = adapter.run(
        "TASK-deadline",
        EventBrief(
            event_name="deadline test",
            target_user=TargetUser.ALL_USER,
            duration=1,
            objective="test deadline",
        ),
    )

    assert result.workflow_status == workflow_service.WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.error_type == "workflow_deadline_exceeded"
    assert trace_events[0]["action"] == "workflow.deadline_exceeded"
    assert trace_events[0]["error_code"] == "ERR-CTRL-DEADLINE-001"
