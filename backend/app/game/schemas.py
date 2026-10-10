"""Validated public contracts for the virtual game API."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GameCommandType(StrEnum):
    STAGE_CHALLENGE = "stage_challenge"
    DUNGEON_CHALLENGE = "dungeon_challenge"
    SPECIAL_DUNGEON_CHALLENGE = "special_dungeon_challenge"


class ApplyEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    task_id: str = Field(pattern=r"^TASK-[A-Z0-9]{12}$")
    result_version: int = Field(ge=1)


class GameActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    command_type: GameCommandType
    content_id: str = Field(min_length=1, max_length=80)
    runtime_event_id: UUID | None = None
    stage_mode: str | None = Field(default=None, pattern=r"^(push|farm)$")
    dungeon_mode: str | None = Field(default=None, pattern=r"^(push|farm)$")


class GrowthActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    growth_action_id: str = Field(min_length=1, max_length=80)


class ShopPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    offer_id: str = Field(min_length=1, max_length=80)


class EquipSkinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    skin_id: str | None = Field(default=None, max_length=80)


class RuntimeErrorResponse(BaseModel):
    error_code: str
    message: str
    detail: list[dict[str, Any]] = Field(default_factory=list)
