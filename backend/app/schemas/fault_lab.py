"""Closed request contracts for the isolated fault laboratory."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.planning import EventBriefInput


class FaultRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=100)
    pattern: Literal["once", "first_n", "always"] = "once"
    first_n: int = Field(default=1, strict=True, ge=1, le=4)
    profile: Literal["current_active", "retry_allowed", "safe_stop"] = "current_active"
    brief: EventBriefInput | None = None


class FaultSuiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[FaultRunRequest] = Field(min_length=1, max_length=100)
