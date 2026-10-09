"""SQLAlchemy repository for PostgreSQL and SQLite-based repository tests."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from backend.app.schemas.workflow_state import TaskRecord, ToolLogEntry, TraceEntry


class Base(DeclarativeBase):
    pass


class WorkflowRunRow(Base):
    __tablename__ = "workflow_runs"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskCreationOrderRow(Base):
    __tablename__ = "task_creation_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class TraceRow(Base):
    __tablename__ = "traces"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_traces_task_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolLogRow(Base):
    __tablename__ = "tool_logs"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_tool_logs_task_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SqlAlchemyRepository:
    def __init__(self, database_url: str, *, create_schema: bool = True) -> None:
        self._local_idempotency_lock = RLock()
        self._local_sequence_lock = RLock()
        options: dict[str, Any] = {"pool_pre_ping": True}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            options.update(
                {
                    "connect_args": {"check_same_thread": False},
                    "poolclass": StaticPool,
                }
            )
        self.engine = create_engine(database_url, **options)
        if create_schema:
            Base.metadata.create_all(self.engine)
            self._backfill_creation_order()

    def _backfill_creation_order(self) -> None:
        with Session(self.engine) as session, session.begin():
            rows = session.scalars(
                select(WorkflowRunRow)
                .outerjoin(
                    TaskCreationOrderRow, WorkflowRunRow.task_id == TaskCreationOrderRow.task_id
                )
                .where(TaskCreationOrderRow.id.is_(None))
                .order_by(WorkflowRunRow.created_at, WorkflowRunRow.task_id)
            )
            for row in rows:
                session.add(TaskCreationOrderRow(task_id=row.task_id))

    def reset(self) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(delete(ToolLogRow))
            session.execute(delete(TraceRow))
            session.execute(delete(IdempotencyRow))
            session.execute(delete(TaskCreationOrderRow))
            session.execute(delete(WorkflowRunRow))

    def create_task(self, task: TaskRecord) -> None:
        payload = task.model_dump(mode="json")
        with Session(self.engine) as session, session.begin():
            session.add(
                WorkflowRunRow(
                    task_id=task.task_id,
                    payload=payload,
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
            )
            session.add(TaskCreationOrderRow(task_id=task.task_id))

    def get_task(self, task_id: str) -> TaskRecord | None:
        with Session(self.engine) as session:
            row = session.get(WorkflowRunRow, task_id)
            return TaskRecord.model_validate(row.payload) if row else None

    def save_task(self, task: TaskRecord) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(WorkflowRunRow, task.task_id)
            if row is None:
                raise KeyError(task.task_id)
            row.payload = task.model_dump(mode="json")
            row.updated_at = task.updated_at

    def list_tasks(self, limit: int) -> list[TaskRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(WorkflowRunRow)
                .join(TaskCreationOrderRow, WorkflowRunRow.task_id == TaskCreationOrderRow.task_id)
                .order_by(WorkflowRunRow.created_at.desc(), TaskCreationOrderRow.id.desc())
                .limit(limit)
            )
            return [TaskRecord.model_validate(row.payload) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        with Session(self.engine) as session, session.begin():
            row = session.get(WorkflowRunRow, task_id)
            if row is None:
                return False
            session.execute(delete(TraceRow).where(TraceRow.task_id == task_id))
            session.execute(delete(ToolLogRow).where(ToolLogRow.task_id == task_id))
            session.execute(delete(IdempotencyRow).where(IdempotencyRow.task_id == task_id))
            session.execute(
                delete(TaskCreationOrderRow).where(TaskCreationOrderRow.task_id == task_id)
            )
            session.delete(row)
            return True

    def append_trace(self, task_id: str, trace: TraceEntry) -> None:
        with self._local_sequence_lock, Session(self.engine) as session, session.begin():
            if (
                session.scalar(
                    select(WorkflowRunRow.task_id)
                    .where(WorkflowRunRow.task_id == task_id)
                    .with_for_update()
                )
                is None
            ):
                raise KeyError(task_id)
            sequence = (
                session.scalar(
                    select(func.max(TraceRow.sequence)).where(TraceRow.task_id == task_id)
                )
                or 0
            ) + 1
            assigned = trace.model_copy(update={"sequence": sequence})
            session.add(
                TraceRow(
                    task_id=task_id, sequence=sequence, payload=assigned.model_dump(mode="json")
                )
            )

    def get_traces(self, task_id: str) -> list[TraceEntry] | None:
        with Session(self.engine) as session:
            task_exists = session.get(WorkflowRunRow, task_id) is not None
            if not task_exists:
                return None
            rows = session.scalars(
                select(TraceRow).where(TraceRow.task_id == task_id).order_by(TraceRow.sequence)
            )
            return [TraceEntry.model_validate(row.payload) for row in rows]

    def append_tool_log(self, task_id: str, tool_log: ToolLogEntry) -> None:
        with self._local_sequence_lock, Session(self.engine) as session, session.begin():
            if (
                session.scalar(
                    select(WorkflowRunRow.task_id)
                    .where(WorkflowRunRow.task_id == task_id)
                    .with_for_update()
                )
                is None
            ):
                raise KeyError(task_id)
            sequence = (
                session.scalar(
                    select(func.max(ToolLogRow.sequence)).where(ToolLogRow.task_id == task_id)
                )
                or 0
            ) + 1
            assigned = tool_log.model_copy(update={"sequence": sequence})
            session.add(
                ToolLogRow(
                    task_id=task_id, sequence=sequence, payload=assigned.model_dump(mode="json")
                )
            )

    def get_tool_logs(self, task_id: str) -> list[ToolLogEntry] | None:
        with Session(self.engine) as session:
            if session.get(WorkflowRunRow, task_id) is None:
                return None
            rows = session.scalars(
                select(ToolLogRow)
                .where(ToolLogRow.task_id == task_id)
                .order_by(ToolLogRow.sequence)
            )
            return [ToolLogEntry.model_validate(row.payload) for row in rows]

    def find_idempotency(self, key: str) -> tuple[str, str] | None:
        with Session(self.engine) as session:
            row = session.get(IdempotencyRow, key)
            return (row.request_hash, row.task_id) if row else None

    def save_idempotency(self, key: str, request_hash: str, task_id: str) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(
                IdempotencyRow(
                    idempotency_key=key,
                    request_hash=request_hash,
                    task_id=task_id,
                    created_at=datetime.now().astimezone(),
                )
            )

    @contextmanager
    def idempotency_lock(self, key: str) -> Iterator[None]:
        if self.engine.dialect.name != "postgresql":
            with self._local_idempotency_lock:
                yield
            return
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        with self.engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
            connection.commit()
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                )
                connection.commit()

    def create_task_with_idempotency(self, task: TaskRecord, key: str, request_hash: str) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(
                WorkflowRunRow(
                    task_id=task.task_id,
                    payload=task.model_dump(mode="json"),
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
            )
            session.add(TaskCreationOrderRow(task_id=task.task_id))
            session.add(
                IdempotencyRow(
                    idempotency_key=key,
                    request_hash=request_hash,
                    task_id=task.task_id,
                    created_at=datetime.now().astimezone(),
                )
            )

    def save_task_with_idempotency(self, task: TaskRecord, key: str, request_hash: str) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(WorkflowRunRow, task.task_id)
            if row is None:
                raise KeyError(task.task_id)
            row.payload = task.model_dump(mode="json")
            row.updated_at = task.updated_at
            session.add(
                IdempotencyRow(
                    idempotency_key=key,
                    request_hash=request_hash,
                    task_id=task.task_id,
                    created_at=datetime.now().astimezone(),
                )
            )

    def ready(self) -> bool:
        try:
            with Session(self.engine) as session:
                session.execute(select(1))
            return True
        except SQLAlchemyError:
            return False
