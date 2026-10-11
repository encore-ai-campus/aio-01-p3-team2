"""Share one database transaction across planning and Runtime writes."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_SESSION: ContextVar[Session | None] = ContextVar("workflow_unit_of_work", default=None)


class DatabaseOutcomeUnknown(ConnectionError):
    """A write could not be reconciled with its durable result."""


@contextmanager
def session_scope(engine: Engine, *, write: bool = False) -> Iterator[Session]:
    current = _SESSION.get()
    if current is not None:
        if current.get_bind().url != engine.url:
            raise ValueError("Workflow and Runtime must use the same database")
        if engine.dialect.name == "sqlite" and current.get_bind() is not engine:
            raise ValueError("SQLite units of work must share the exact engine instance")
        yield current
        return
    with Session(engine, expire_on_commit=False) as session:
        if write:
            with session.begin():
                token = _SESSION.set(session)
                try:
                    yield session
                finally:
                    _SESSION.reset(token)
        else:
            yield session
