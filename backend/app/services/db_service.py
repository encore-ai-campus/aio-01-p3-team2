"""Repository selection and the in-memory development implementation."""

import os
from copy import deepcopy
from threading import RLock

from backend.app.repositories.interface import WorkflowRepository
from backend.app.schemas.workflow_state import TaskRecord, ToolLogEntry, TraceEntry


class InMemoryRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._traces: dict[str, list[TraceEntry]] = {}
        self._tool_logs: dict[str, list[ToolLogEntry]] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._lock = RLock()

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._traces.clear()
            self._tool_logs.clear()
            self._idempotency.clear()

    def create_task(self, task: TaskRecord) -> None:
        with self._lock:
            self._tasks[task.task_id] = deepcopy(task)
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
            tasks = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
            return deepcopy(tasks[:limit])

    def append_trace(self, task_id: str, trace: TraceEntry) -> None:
        with self._lock:
            self._traces.setdefault(task_id, []).append(deepcopy(trace))

    def get_traces(self, task_id: str) -> list[TraceEntry] | None:
        with self._lock:
            if task_id not in self._tasks:
                return None
            return deepcopy(self._traces.get(task_id, []))

    def append_tool_log(self, task_id: str, tool_log: ToolLogEntry) -> None:
        with self._lock:
            self._tool_logs.setdefault(task_id, []).append(deepcopy(tool_log))

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


def build_repository() -> WorkflowRepository:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return InMemoryRepository()

    from backend.app.repositories.postgres import SqlAlchemyRepository

    return SqlAlchemyRepository(database_url)


repository = build_repository()
