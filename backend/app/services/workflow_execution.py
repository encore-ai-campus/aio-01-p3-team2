"""Durable local Workflow reservations, progress and bounded execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore, RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from backend.app.agents.supervisor import Supervisor, SupervisorWorkflowState
from backend.app.config.deployment import runtime_schema_creation_allowed
from backend.app.faults.error_catalog import classify_workflow_exception, error_definition
from backend.app.services.llm_service import LLMOutputContractError, workflow_llm_call_budget
from backend.app.services.recovery_policy import pinned_policy_reference


class ExecutionBase(DeclarativeBase):
    pass


class WorkflowOperation(ExecutionBase):
    __tablename__ = "workflow_operations"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


_EXECUTION: ContextVar[dict[str, Any] | None] = ContextVar(
    "durable_workflow_execution", default=None
)
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="planning")
_CAPACITY = BoundedSemaphore(4)
_MEMORY: dict[str, dict[str, Any]] = {}
_LOCK = RLock()
_TERMINAL = {
    "WAITING_APPROVAL",
    "WAITING_REVIEW",
    "SAFE_STOP",
    "COMPLETED",
    "CANCELLED",
    "REJECTED",
    "REVISION_REQUESTED",
    "APPLIED",
}
WORKFLOW_DEADLINE_SECONDS = 180


def _engine():
    from backend.app.services.canonical_planning import _engine as planning_engine

    engine = planning_engine(os.environ["DATABASE_URL"])
    if runtime_schema_creation_allowed():
        ExecutionBase.metadata.create_all(engine)
    return engine


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def execution_ids() -> dict[str, Any] | None:
    execution = _EXECUTION.get()
    return execution["payload"] if execution else None


def _read(key: str) -> dict[str, Any] | None:
    if not os.getenv("DATABASE_URL", "").strip():
        value = _MEMORY.get(key)
        return json.loads(json.dumps(value)) if value else None
    with Session(_engine()) as session:
        row = session.get(WorkflowOperation, key)
        return (
            {
                "key": key,
                "hash": row.request_hash,
                "owner": row.owner,
                "deadline": _utc(row.deadline_at).isoformat(),
                "status": row.status,
                "payload": row.payload,
            }
            if row
            else None
        )


def _write(
    key: str,
    owner: str,
    payload: dict[str, Any],
    *,
    expected_version: int | None = None,
    status: str | None = None,
) -> None:
    from backend.app.fault_injection.injector import observe_exception
    from backend.app.schemas.workflow_state import SupervisorStateConflict
    from backend.app.services.unit_of_work import DatabaseOutcomeUnknown

    retry_count = 0
    operation_id = str(uuid4())
    execution = _EXECUTION.get()

    def observe(event: str, action: str, count: int) -> None:
        row = {
            "event": event,
            "status": action,
            "error_code": error_definition("db_connection_error").code,
            "operation_id": operation_id,
            "attempt_count": count,
            "retry_count": count if action == "RETRY" else retry_count,
            "policy_version": (pinned_policy_reference() or {}).get("version"),
            "matched_rule_id": error_definition("db_connection_error").code,
            "target": "storage.write",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        # Record the decision without recursively trying to persist the failed storage operation.
        containers = [payload]
        if execution is not None and execution["payload"] is not payload:
            containers.append(execution["payload"])
        for container in containers:
            trace = container.setdefault("trace", [])
            trace.append({**row, "sequence": len(trace) + 1})
        from backend.app.fault_injection.injector import observe_execution_event

        observe_execution_event(row)

    while True:
        try:
            _write_once(key, owner, payload, expected_version=expected_version, status=status)
            if retry_count:
                observe("storage.recovery_resolved", "SUCCESS", retry_count + 1)
            return
        except SQLAlchemyError as exc:
            observe_exception(exc)
            # Reconcile acknowledgement loss before considering any additional write.
            try:
                committed = _read(key)
            except SQLAlchemyError as read_error:
                raise DatabaseOutcomeUnknown(
                    "Workflow write outcome could not be read"
                ) from read_error
            if (
                committed
                and committed["owner"] == owner
                and committed["payload"] == payload
                and (status is None or committed["status"] == status)
            ):
                return
            if (
                not committed
                or committed["owner"] != owner
                or committed["status"] not in {"ACCEPTED", "RUNNING"}
            ):
                raise SupervisorStateConflict("Storage recovery lost execution ownership") from exc
            if (
                expected_version is not None
                and committed["payload"]["supervisor_state"]["state_version"] != expected_version
            ):
                raise SupervisorStateConflict("Storage recovery lost state version") from exc
            if not isinstance(exc, OperationalError):
                raise
            deadline = datetime.fromisoformat(execution["deadline"]) if execution else None
            if deadline and datetime.now(UTC) >= deadline:
                raise LLMOutputContractError(
                    "Storage recovery exceeded Workflow deadline",
                    error_type="workflow_deadline_exceeded",
                ) from exc
            route = Supervisor({"context_access": {}}).decide_failure(
                "db_connection_error", retry_count=retry_count
            )
            observe("storage.recovery_decided", route.action, retry_count + 1)
            if route.action != "RETRY":
                raise
            retry_count = route.retry_count
            delay = min(0.25 * 2 ** (retry_count - 1), 4.0)
            delay = random.uniform(delay / 2, delay)
            if deadline and datetime.now(UTC) + timedelta(seconds=delay) >= deadline:
                raise LLMOutputContractError(
                    "Storage recovery exceeded Workflow deadline",
                    error_type="workflow_deadline_exceeded",
                ) from exc
            time.sleep(delay)


def _write_once(
    key: str,
    owner: str,
    payload: dict[str, Any],
    *,
    expected_version: int | None = None,
    status: str | None = None,
) -> None:
    from backend.app.fault_injection.injector import perturb
    from backend.app.schemas.workflow_state import SupervisorStateConflict

    perturb("storage.write")

    if os.getenv("DATABASE_URL", "").strip():
        with Session(_engine()) as session, session.begin():
            row = session.scalar(
                select(WorkflowOperation)
                .where(WorkflowOperation.idempotency_key == key)
                .with_for_update()
            )
            if row is None or row.owner != owner or row.status not in {"ACCEPTED", "RUNNING"}:
                raise SupervisorStateConflict("Workflow execution ownership changed")
            if (
                expected_version is not None
                and row.payload["supervisor_state"]["state_version"] != expected_version
            ):
                raise SupervisorStateConflict("Workflow progress version changed")
            row.payload = payload
            row.status = status or "RUNNING"
    else:
        with _LOCK:
            row = _MEMORY[key]
            if row["owner"] != owner or row["status"] not in {"ACCEPTED", "RUNNING"}:
                raise SupervisorStateConflict("Workflow execution ownership changed")
            if (
                expected_version is not None
                and row["payload"]["supervisor_state"]["state_version"] != expected_version
            ):
                raise SupervisorStateConflict("Workflow progress version changed")
            row["payload"] = json.loads(json.dumps(payload))
            row["status"] = status or "RUNNING"


@contextmanager
def finalize_execution(record: dict[str, Any]):
    """Commit the result and release its reservation in the same transaction."""
    from backend.app.schemas.workflow_state import SupervisorStateConflict
    from backend.app.services.unit_of_work import session_scope

    execution = _EXECUTION.get()
    if execution is None:
        yield
        return
    live_trace = execution["payload"]["trace"]
    annotations = [
        item
        for item in record.get("trace", [])
        if not item.get("event", "").startswith(("supervisor.", "handoff."))
    ]
    actual = []
    for item in live_trace:
        annotation = next(
            (row for row in annotations if row.get("event") == item.get("event")), None
        )
        if annotation is not None:
            annotations.remove(annotation)
        actual.append({**(annotation or {}), **item})
    combined_trace = [*actual, *annotations]
    record["trace"] = [{**item, "sequence": index} for index, item in enumerate(combined_trace, 1)]
    record["operation_status"] = "FINISHED"
    record["current_status"] = record["supervisor_state"]["status"]

    def check(owner, status, payload):
        if owner != execution["owner"] or status not in {"ACCEPTED", "RUNNING"}:
            raise SupervisorStateConflict("Workflow execution ownership changed")
        if datetime.fromisoformat(execution["deadline"]) <= datetime.now(UTC):
            raise LLMOutputContractError(
                "Workflow deadline exceeded", error_type="workflow_deadline_exceeded"
            )
        if payload["supervisor_state"] != record["supervisor_state"]:
            raise SupervisorStateConflict("Final Workflow state differs from durable state")

    if os.getenv("DATABASE_URL", "").strip():
        with session_scope(_engine(), write=True) as session:
            row = session.scalar(
                select(WorkflowOperation)
                .where(WorkflowOperation.idempotency_key == execution["key"])
                .with_for_update()
            )
            if row is None:
                raise SupervisorStateConflict("Workflow reservation disappeared")
            check(row.owner, row.status, row.payload)
            yield
            row.payload = record
            row.status = "FINISHED"
    else:
        with _LOCK:
            row = _MEMORY[execution["key"]]
            check(row["owner"], row["status"], row["payload"])
            yield
            row["payload"] = json.loads(json.dumps(record))
            row["status"] = "FINISHED"


def publish_decision(decision: Any) -> None:
    execution = _EXECUTION.get()
    if execution is None:
        return
    payload = {
        **execution["payload"],
        "supervisor_state": decision.state.to_dict(),
        "workflow_status": decision.state.status,
        "current_status": decision.state.status,
        "operation_status": "RUNNING",
        "trace": [
            *execution["payload"]["trace"],
            {"sequence": len(execution["payload"]["trace"]) + 1, **decision.to_trace()},
        ],
    }
    committed_event = payload["trace"][-1]
    _write(
        execution["key"],
        execution["owner"],
        payload,
        expected_version=decision.state.state_version - 1,
    )
    execution["payload"] = payload

    from backend.app.fault_injection.injector import observe_execution_event, observe_transition

    observe_execution_event(committed_event)
    observe_transition(decision)


def record_handoff(handoff: Any, event: str) -> None:
    execution = _EXECUTION.get()
    if execution is None:
        return
    payload = {
        **execution["payload"],
        "trace": [
            *execution["payload"]["trace"],
            {
                "sequence": len(execution["payload"]["trace"]) + 1,
                "event": event,
                "status": handoff.handoff_status.value,
                "task_id": handoff.task_id,
                "from_agent": handoff.from_agent,
                "to_agent": handoff.to_agent,
                "contract_version": handoff.input_contract_version,
            },
        ],
    }
    committed_event = payload["trace"][-1]
    _write(
        execution["key"],
        execution["owner"],
        payload,
        expected_version=payload["supervisor_state"]["state_version"],
    )
    execution["payload"] = payload
    from backend.app.fault_injection.injector import observe_execution_event

    observe_execution_event(committed_event)


def record_observation(event: str, status: str, **attributes: Any) -> None:
    """Persist stage observations when they occur, preserving causal UI order."""
    execution = _EXECUTION.get()
    if execution is None:
        return
    from backend.app.fault_injection.injector import perturb

    perturb("trace.write")
    payload = {
        **execution["payload"],
        "trace": [
            *execution["payload"]["trace"],
            {
                "sequence": len(execution["payload"]["trace"]) + 1,
                "event": event,
                "status": status,
                **attributes,
            },
        ],
    }
    committed_event = payload["trace"][-1]
    _write(
        execution["key"],
        execution["owner"],
        payload,
        expected_version=payload["supervisor_state"]["state_version"],
    )
    execution["payload"] = payload
    from backend.app.fault_injection.injector import observe_execution_event

    observe_execution_event(committed_event)


def append_recovery_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Keep recovery details at their occurrence, before the next attempt or terminal state."""
    events.append(event)
    record_observation(
        str(event["event"]),
        str(event["status"]),
        **{key: value for key, value in event.items() if key not in {"event", "status"}},
    )


def validate_handoff(handoff: Any, **kwargs: Any) -> None:
    from backend.app.fault_injection.injector import perturb

    handoff = perturb("handoff.validate", handoff)
    record_handoff(handoff, "handoff.requested")
    try:
        handoff.validate_receiver(**kwargs)
    except ValueError as exc:
        record_handoff(handoff.reject("수신 계약 검증 실패"), "handoff.rejected")
        raise LLMOutputContractError(
            "Handoff contract rejected", error_type="handoff_contract_invalid"
        ) from exc


def run_async(operation: Awaitable[Any]) -> Any:
    from backend.app.fault_injection.injector import perturb

    execution = _EXECUTION.get()
    if execution is None:
        return asyncio.run(operation)
    execution = perturb("workflow.deadline", execution)
    remaining = (datetime.fromisoformat(execution["deadline"]) - datetime.now(UTC)).total_seconds()

    async def bounded():
        try:
            return await asyncio.wait_for(operation, timeout=max(0, remaining))
        except TimeoutError as exc:
            raise LLMOutputContractError(
                "Workflow deadline exceeded", error_type="workflow_deadline_exceeded"
            ) from exc

    return asyncio.run(bounded())


def _stop(execution: dict[str, Any], error_type: str) -> dict[str, Any]:
    token = _EXECUTION.set(execution)
    try:
        state = SupervisorWorkflowState.from_mapping(execution["payload"]["supervisor_state"])
        if state.status not in Supervisor._TERMINAL_STATES:
            definition = error_definition(error_type)
            decision = Supervisor({"context_access": {}}).decide_state_transition(
                state,
                "safe_stop",
                error_code=definition.code,
                policy_version=(execution["payload"].get("recovery_policy_ref") or {}).get(
                    "version"
                ),
                payload={"reason": definition.safe_message},
            )
            publish_decision(decision)
        definition = error_definition(error_type)
        payload = {
            **execution["payload"],
            "error": {"code": definition.code, "message": definition.safe_message},
            "operation_status": "STOPPED",
        }
        _write(execution["key"], execution["owner"], payload, status="STOPPED")
        return payload
    finally:
        _EXECUTION.reset(token)


def _execute(execution: dict[str, Any], brief: Any) -> dict[str, Any]:
    from backend.app.services.canonical_planning import (
        create_event_workflow,
        resolve_event_workflow_submission,
    )

    token = _EXECUTION.set(execution)
    try:
        from backend.app.fault_injection.injector import perturb

        perturb("workflow.execute")
        with workflow_llm_call_budget(max_calls=8):
            result = create_event_workflow(brief, execution["key"])
        return result
    except Exception as exc:  # noqa: BLE001 - normalize all worker failures into durable safe stop
        from backend.app.fault_injection.injector import observe_exception

        observe_exception(exc)
        from backend.app.schemas.workflow_state import SupervisorStateConflict

        if isinstance(exc, SupervisorStateConflict):
            return _read(execution["key"])["payload"]
        # A lost commit acknowledgement must not overwrite an already persisted result.
        recovered = resolve_event_workflow_submission(brief, execution["key"])
        if recovered is not None:
            if _read(execution["key"])["status"] in {"ACCEPTED", "RUNNING"}:
                _write(execution["key"], execution["owner"], recovered, status="FINISHED")
            return recovered
        return _stop(execution, classify_workflow_exception(exc))
    finally:
        _EXECUTION.reset(token)


def submit(brief: Any, key: str, *, asynchronous: bool) -> dict[str, Any]:
    request_hash = hashlib.sha256(
        json.dumps(
            brief.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with _LOCK:
        from backend.app.services.canonical_planning import resolve_event_workflow_submission

        prior_result = resolve_event_workflow_submission(brief, key)
        if prior_result is not None:
            return prior_result
        previous = _read(key)
        if previous:
            if previous["hash"] != request_hash:
                raise ValueError("Idempotency-Key was already used for a different request")
            return previous["payload"]
        if not _CAPACITY.acquire(blocking=False):
            raise RuntimeError(
                "Workflow 실행 용량이 가득 찼습니다. 잠시 후 같은 요청으로 다시 시도하세요."
            )
        workflow_id = f"WF-{uuid4().hex[:12].upper()}"
        now = datetime.now(UTC)
        state = Supervisor({"context_access": {}}).start_workflow(workflow_id)
        payload = {
            "workflow_id": workflow_id,
            "task_id": f"TASK-{uuid4().hex[:12].upper()}",
            "brief_id": f"BRIEF-{uuid4().hex[:12].upper()}",
            "plan_id": f"PLAN-{uuid4().hex[:12].upper()}",
            "workflow_status": "RECEIVED",
            "current_status": "RECEIVED",
            "supervisor_state": state.to_dict(),
            "trace": [],
            "brief": brief.model_dump(mode="json"),
            "created_at": now.isoformat(),
            "operation_status": "ACCEPTED",
            "recovery_policy_ref": pinned_policy_reference(),
        }
        execution = {
            "key": key,
            "hash": request_hash,
            "owner": str(uuid4()),
            "deadline": (now + timedelta(seconds=WORKFLOW_DEADLINE_SECONDS)).isoformat(),
            "status": "ACCEPTED",
            "payload": payload,
        }
        try:
            if os.getenv("DATABASE_URL", "").strip():
                try:
                    with Session(_engine()) as session, session.begin():
                        session.add(
                            WorkflowOperation(
                                idempotency_key=key,
                                workflow_id=workflow_id,
                                request_hash=request_hash,
                                owner=execution["owner"],
                                status="ACCEPTED",
                                deadline_at=datetime.fromisoformat(execution["deadline"]),
                                payload=payload,
                            )
                        )
                except IntegrityError:
                    previous = _read(key)
                    if previous is None or previous["hash"] != request_hash:
                        raise ValueError("Workflow reservation conflict") from None
                    _CAPACITY.release()
                    return previous["payload"]
            else:
                _MEMORY[key] = execution
        except Exception:
            _CAPACITY.release()
            raise
    if asynchronous:
        context = copy_context()

        def work():
            try:
                context.run(_execute, execution, brief)
            finally:
                _CAPACITY.release()

        _POOL.submit(work)
        return payload
    try:
        return _execute(execution, brief)
    finally:
        _CAPACITY.release()


def read_progress(workflow_id: str) -> dict[str, Any] | None:
    if os.getenv("DATABASE_URL", "").strip():
        with Session(_engine()) as session:
            row = session.scalar(
                select(WorkflowOperation).where(WorkflowOperation.workflow_id == workflow_id)
            )
            key = row.idempotency_key if row else None
    else:
        key = next(
            (key for key, row in _MEMORY.items() if row["payload"]["workflow_id"] == workflow_id),
            None,
        )
    if key is None:
        return None
    execution = _read(key)
    if execution["status"] in {"ACCEPTED", "RUNNING"}:
        from backend.app.services.canonical_planning import _load

        committed = _load(workflow_id)
        if committed is not None:
            _write(key, execution["owner"], committed, status="FINISHED")
            return committed
    if execution["status"] in {"ACCEPTED", "RUNNING"} and datetime.fromisoformat(
        execution["deadline"]
    ) <= datetime.now(UTC):
        from backend.app.schemas.workflow_state import SupervisorStateConflict

        try:
            return _stop(execution, "workflow_deadline_exceeded")
        except SupervisorStateConflict:
            # Another request may have committed the final state during this read.
            latest = _read(key)
            if latest["status"] in {"ACCEPTED", "RUNNING"}:
                return _stop(latest, "workflow_deadline_exceeded")
            return latest["payload"]
    return execution["payload"]


def list_progress(limit: int = 20) -> list[dict[str, Any]]:
    if os.getenv("DATABASE_URL", "").strip():
        with Session(_engine()) as session:
            ids = list(
                session.scalars(
                    select(WorkflowOperation.workflow_id)
                    .order_by(WorkflowOperation.deadline_at.desc())
                    .limit(limit)
                )
            )
    else:
        ids = [row["payload"]["workflow_id"] for row in list(_MEMORY.values())[-limit:]]
    return [record for identifier in ids if (record := read_progress(identifier)) is not None]


def reconcile_expired() -> int:
    if os.getenv("DATABASE_URL", "").strip():
        with Session(_engine()) as session:
            ids = list(
                session.scalars(
                    select(WorkflowOperation.workflow_id).where(
                        WorkflowOperation.status.in_(["ACCEPTED", "RUNNING"]),
                        WorkflowOperation.deadline_at <= datetime.now(UTC),
                    )
                )
            )
    else:
        ids = [
            row["payload"]["workflow_id"]
            for row in _MEMORY.values()
            if row["status"] in {"ACCEPTED", "RUNNING"}
            and datetime.fromisoformat(row["deadline"]) <= datetime.now(UTC)
        ]
    for identifier in ids:
        read_progress(identifier)
    return len(ids)


def cancel(workflow_id: str, expected_version: int) -> dict[str, Any]:
    """Cancel an admitted execution; fencing prevents later worker results."""
    from backend.app.schemas.workflow_state import SupervisorStateConflict

    progress = read_progress(workflow_id)
    if progress is None:
        raise LookupError("Workflow was not found")
    if progress["supervisor_state"]["status"] == "CANCELLED":
        return progress
    if progress["supervisor_state"]["state_version"] != expected_version:
        raise SupervisorStateConflict("Workflow progress version changed")
    if os.getenv("DATABASE_URL", "").strip():
        with Session(_engine()) as session:
            key = session.scalar(
                select(WorkflowOperation.idempotency_key).where(
                    WorkflowOperation.workflow_id == workflow_id
                )
            )
    else:
        key = next(
            key for key, row in _MEMORY.items() if row["payload"]["workflow_id"] == workflow_id
        )
    execution = _read(key)
    if execution["status"] not in {"ACCEPTED", "RUNNING"}:
        raise SupervisorStateConflict("Only an active execution can be cancelled")
    token = _EXECUTION.set(execution)
    try:
        state = SupervisorWorkflowState.from_mapping(progress["supervisor_state"])
        decision = Supervisor({"context_access": {}}).decide_state_transition(state, "cancelled")
        publish_decision(decision)
        payload = {**execution["payload"], "operation_status": "STOPPED"}
        _write(key, execution["owner"], payload, status="STOPPED")
        return payload
    finally:
        _EXECUTION.reset(token)
