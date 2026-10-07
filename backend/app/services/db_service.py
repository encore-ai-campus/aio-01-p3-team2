"""Thread-safe in-memory repository used until PostgreSQL is connected."""

from copy import deepcopy
from threading import RLock

from backend.app.schemas.workflow_state import TaskRecord, TraceEntry


class InMemoryRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._traces: dict[str, list[TraceEntry]] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._lock = RLock()

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._traces.clear()
            self._idempotency.clear()

    def create_task(self, task: TaskRecord) -> None:
        with self._lock:
            self._tasks[task.task_id] = deepcopy(task)
            self._traces[task.task_id] = []

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task else None

    def save_task(self, task: TaskRecord) -> None:
        with self._lock:
            if task.task_id not in self._tasks:
                raise KeyError(task.task_id)
            self._tasks[task.task_id] = deepcopy(task)

    def append_trace(self, task_id: str, trace: TraceEntry) -> None:
        with self._lock:
            self._traces.setdefault(task_id, []).append(deepcopy(trace))

    def get_traces(self, task_id: str) -> list[TraceEntry] | None:
        with self._lock:
            if task_id not in self._tasks:
                return None
            return deepcopy(self._traces.get(task_id, []))

    def find_idempotency(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            return self._idempotency.get(key)

    def save_idempotency(self, key: str, request_hash: str, task_id: str) -> None:
        with self._lock:
            self._idempotency[key] = (request_hash, task_id)


repository = InMemoryRepository()
