"""Stable error code resolution is deterministic and unknown errors fail closed."""

from backend.app.faults.error_catalog import ERROR_CATALOG, error_definition


def test_legacy_error_aliases_resolve_to_stable_codes() -> None:
    assert error_definition("mission_unreachable").code == "ERR-DOMAIN-RULE-001"
    assert error_definition("mcp_timeout").code == "ERR-DEP-MCP-001"
    assert error_definition("db_connection_error").code == "ERR-STORE-DB-001"


def test_unknown_error_maps_to_safe_stop_catalog_entry() -> None:
    definition = error_definition("unregistered_failure")
    assert definition.code == "ERR-UNKNOWN-WORKFLOW-001"
    assert definition.retry_class == "NEVER"


def test_catalog_codes_are_unique_and_follow_documented_shape() -> None:
    codes = [entry.code for entry in ERROR_CATALOG.values()]
    assert len(codes) == len(set(codes))
    assert all(code.startswith("ERR-") and code.rsplit("-", 1)[-1].isdigit() for code in codes)
