"""Checked-in JSON Schemas stay synchronized with canonical Pydantic contracts."""

from backend.scripts.export_planning_contract_schemas import export_schemas


def test_checked_in_contract_schemas_match_models() -> None:
    assert export_schemas(check=True) == []
