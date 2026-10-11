from contextlib import contextmanager
from unittest.mock import Mock

from backend.app.api import fault_lab


def test_fault_status_reads_policy_without_modifying_environment(monkeypatch):
    connection = Mock()
    connection.execute.return_value.scalar_one_or_none.return_value = 7

    @contextmanager
    def scope():
        yield connection

    environment = Mock(return_value={"state": "CLEAN"})
    monkeypatch.setattr(fault_lab.store, "connection_scope", scope)
    monkeypatch.setattr(fault_lab.store, "environment", environment)
    result = fault_lab.status()
    assert result == {
        "environment": "isolated",
        "database": "eventquest_fault_test",
        "state": "CLEAN",
        "active_policy_version": 7,
    }
    assert str(connection.execute.call_args.args[0]).startswith("SELECT version")
    connection.execute.assert_called_once()
    environment.assert_called_once()
