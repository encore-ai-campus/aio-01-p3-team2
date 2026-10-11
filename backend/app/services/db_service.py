"""Repository selection and the in-memory development implementation."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

from backend.app.config.deployment import deployment_mode
from backend.app.repositories.interface import WorkflowRepository
from backend.app.schemas.workflow_state import (
    SupervisorStateConflict,
    TaskRecord,
    ToolLogEntry,
    TraceEntry,
)


class InMemoryRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._traces: dict[str, list[TraceEntry]] = {}
        self._tool_logs: dict[str, list[ToolLogEntry]] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._creation_order: dict[str, int] = {}
        self._next_creation_order = 0
        self._lock = RLock()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        with self._lock:
            snapshot = deepcopy((self._tasks, self._traces, self._idempotency))
            try:
                yield
            except Exception:
                self._tasks, self._traces, self._idempotency = snapshot
                raise

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._traces.clear()
            self._tool_logs.clear()
            self._idempotency.clear()
            self._creation_order.clear()
            self._next_creation_order = 0

    def create_task(self, task: TaskRecord) -> None:
        with self._lock:
            self._tasks[task.task_id] = deepcopy(task)
            self._next_creation_order += 1
            self._creation_order[task.task_id] = self._next_creation_order
            self._traces[task.task_id] = []
            self._tool_logs[task.task_id] = []

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task else None

    def save_task(self, task: TaskRecord) -> None:
        with self._lock:
            if task.task_id not in self._tasks:
                raise KeyError(task.task_id)
            self._tasks[task.task_id] = deepcopy(task)

    def list_tasks(self, limit: int) -> list[TaskRecord]:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda item: (item.created_at, self._creation_order[item.task_id]),
                reverse=True,
            )
            return deepcopy(tasks[:limit])

    def list_running_tasks(self) -> list[TaskRecord]:
        with self._lock:
            return deepcopy(
                [
                    task
                    for task in self._tasks.values()
                    if task.workflow_status in {"RUNNING", "RETRYING"}
                ]
            )

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
            self._traces.pop(task_id, None)
            self._tool_logs.pop(task_id, None)
            self._creation_order.pop(task_id, None)
            for key, (_, stored_task_id) in list(self._idempotency.items()):
                if stored_task_id == task_id:
                    del self._idempotency[key]
            return True

    def append_trace(self, task_id: str, trace: TraceEntry) -> None:
        from backend.app.schemas.workflow_state import expand_supervisor_decisions

        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            traces = self._traces.setdefault(task_id, [])
            latest_state_version = next(
                (item.state_version for item in reversed(traces) if item.state_version is not None),
                0,
            )
            additions = []
            for entry in expand_supervisor_decisions(trace):
                prior = next(
                    (
                        row
                        for row in traces
                        if (entry.decision_id and row.decision_id == entry.decision_id)
                        or (entry.event_id and row.event_id == entry.event_id)
                    ),
                    None,
                )
                if prior is not None:
                    if prior.model_dump(
                        exclude={"sequence", "supervisor_decisions"}
                    ) != entry.model_dump(exclude={"sequence", "supervisor_decisions"}):
                        raise SupervisorStateConflict(
                            "Decision ID was reused with different content"
                        )
                    continue
                if entry.state_version is not None and (
                    entry.expected_state_version != latest_state_version
                    or entry.state_version != latest_state_version + 1
                ):
                    raise SupervisorStateConflict(
                        "Supervisor Task state version changed before trace commit"
                    )
                if entry.state_version is not None:
                    latest_state_version = entry.state_version
                additions.append(
                    entry.model_copy(
                        update={"sequence": len(traces) + len(additions) + 1}, deep=True
                    )
                )
            traces.extend(additions)

    def get_traces(self, task_id: str) -> list[TraceEntry] | None:
        with self._lock:
            if task_id not in self._tasks:
                return None
            return deepcopy(self._traces.get(task_id, []))

    def append_tool_log(self, task_id: str, tool_log: ToolLogEntry) -> None:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            tool_logs = self._tool_logs.setdefault(task_id, [])
            tool_logs.append(
                tool_log.model_copy(update={"sequence": len(tool_logs) + 1}, deep=True)
            )

    def get_tool_logs(self, task_id: str) -> list[ToolLogEntry] | None:
        with self._lock:
            if task_id not in self._tasks:
                return None
            return deepcopy(self._tool_logs.get(task_id, []))

    def find_idempotency(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            return self._idempotency.get(key)

    def save_idempotency(self, key: str, request_hash: str, task_id: str) -> None:
        with self._lock:
            self._idempotency[key] = (request_hash, task_id)

    @contextmanager
    def idempotency_lock(self, key: str) -> Iterator[None]:
        del key
        with self._lock:
            yield

    def create_task_with_idempotency(self, task: TaskRecord, key: str, request_hash: str) -> None:
        with self._lock:
            if key in self._idempotency:
                raise ValueError("멱등성 키가 이미 사용됐습니다.")
            self.create_task(task)
            self._idempotency[key] = (request_hash, task.task_id)

    def save_task_with_idempotency(self, task: TaskRecord, key: str, request_hash: str) -> None:
        with self._lock:
            if key in self._idempotency:
                raise ValueError("멱등성 키가 이미 사용됐습니다.")
            self.save_task(task)
            self._idempotency[key] = (request_hash, task.task_id)


def build_repository() -> WorkflowRepository:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        if deployment_mode() == "cloud":
            raise RuntimeError("Cloud runtime requires DATABASE_URL")
        return InMemoryRepository()

    from backend.app.repositories.postgres import SqlAlchemyRepository

    return SqlAlchemyRepository(database_url)


repository = build_repository()
