"""Canonical schema validation helpers for event planning contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from backend.app.schemas.planning import (
    CapabilityCatalog,
    EventBriefInput,
    EventPlan,
    ValidationFinding,
)
from pydantic import BaseModel

ContractT = TypeVar("ContractT", bound=BaseModel)

CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "event-brief.v1": EventBriefInput,
    "event-plan.v1": EventPlan,
    "validation-finding.v1": ValidationFinding,
    "capability-catalog.v1": CapabilityCatalog,
}
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "v1"


def validate_contract(contract_version: str, payload: object) -> BaseModel:
    """Validate data using the registered immutable contract version."""
    model = CONTRACT_MODELS.get(contract_version)
    if model is None:
        raise ValueError(f"Unsupported contract version: {contract_version}")
    if not isinstance(payload, dict):
        raise TypeError("Contract payload must be an object")
    return model.model_validate(payload)


def load_contract_schema(contract_version: str) -> dict[str, object]:
    """Load the checked-in JSON Schema paired with a registered Pydantic model."""
    if contract_version not in CONTRACT_MODELS:
        raise ValueError(f"Unsupported contract version: {contract_version}")
    path = SCHEMA_DIR / f"{contract_version.replace('.', '-')}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise TypeError(f"Invalid contract schema: {contract_version}")
    return schema
