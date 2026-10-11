"""PostgreSQL-backed virtual game state, separate from Workflow tables."""

import os
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


class GameBase(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB(), "postgresql")


class VirtualUserRow(GameBase):
    __tablename__ = "game_virtual_users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "equipped_skin_id"],
            ["game_skin_ownerships.user_id", "game_skin_ownerships.skin_id"],
            name="fk_game_user_equipped_owned_skin",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    segment_id: Mapped[str] = mapped_column(String(40), nullable=False)
    experience_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    highest_stage_order: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_hunt_stage_order: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    highest_dungeon_floor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_dungeon_floor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    dungeon_hunt_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="push")
    dungeon_success_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    hunt_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="push")
    weapon_upgrade_level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    armor_upgrade_level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    equipped_skin_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogSnapshotRow(GameBase):
    __tablename__ = "game_runtime_catalog_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeEventRow(GameBase):
    __tablename__ = "game_runtime_events"
    __table_args__ = (
        UniqueConstraint("source_task_id", "result_version", name="uq_game_event_source_version"),
    )

    runtime_event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    game_ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    game_data_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventParticipationRow(GameBase):
    __tablename__ = "game_event_participations"
    __table_args__ = (UniqueConstraint("runtime_event_id", "user_id", name="uq_game_event_user"),)

    participation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_event_id: Mapped[str] = mapped_column(
        ForeignKey("game_runtime_events.runtime_event_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_virtual_users.user_id", ondelete="RESTRICT"), nullable=False
    )
    eligibility_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameRequestRow(GameBase):
    __tablename__ = "game_request_results"

    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionAttemptRow(GameBase):
    __tablename__ = "game_action_attempts"

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    runtime_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    command_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content_id: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    attempt_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)


class ActionRecordRow(GameBase):
    __tablename__ = "game_action_records"
    __table_args__ = (
        UniqueConstraint("attempt_id", "action_type", "subtype", name="uq_game_attempt_action"),
    )

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("game_action_attempts.attempt_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    runtime_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    subtype: Mapped[str] = mapped_column(String(40), nullable=False, default="default")
    content_id: Mapped[str] = mapped_column(String(80), nullable=False)
    game_ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    game_data_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    action_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)


class MissionProgressRow(GameBase):
    __tablename__ = "game_mission_progress"
    __table_args__ = (
        UniqueConstraint("runtime_event_id", "mission_id", name="uq_game_event_mission"),
    )

    progress_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_event_id: Mapped[str] = mapped_column(
        ForeignKey("game_runtime_events.runtime_event_id", ondelete="CASCADE"), nullable=False
    )
    mission_id: Mapped[str] = mapped_column(String(80), nullable=False)
    current_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_value: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InventoryBalanceRow(GameBase):
    __tablename__ = "game_inventory_balances"

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkinOwnershipRow(GameBase):
    __tablename__ = "game_skin_ownerships"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_virtual_users.user_id", ondelete="RESTRICT"), primary_key=True
    )
    skin_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InventoryLedgerRow(GameBase):
    __tablename__ = "game_inventory_ledger"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "item_id", name="uq_game_inventory_source"),
    )

    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_virtual_users.user_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(String(80), nullable=False)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ShopPurchaseRow(GameBase):
    __tablename__ = "game_shop_purchases"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_game_shop_user_request"),
        ForeignKeyConstraint(
            ["user_id", "skin_id"],
            ["game_skin_ownerships.user_id", "game_skin_ownerships.skin_id"],
            name="fk_game_shop_owned_skin",
        ),
    )

    purchase_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_virtual_users.user_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    skin_id: Mapped[str] = mapped_column(String(80), nullable=False)
    price_item_id: Mapped[str] = mapped_column(String(80), nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RewardRecordRow(GameBase):
    __tablename__ = "game_reward_records"
    __table_args__ = (
        UniqueConstraint("grant_source_type", "source_id", "item_id", name="uq_game_reward_grant"),
    )

    reward_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    runtime_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    mission_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    item_id: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    grant_source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GrowthRecordRow(GameBase):
    __tablename__ = "game_growth_records"

    growth_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    growth_action_id: Mapped[str] = mapped_column(String(80), nullable=False)
    item_id: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity_spent: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upgrade_level_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upgrade_level_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_stats: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    after_stats: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def make_engine(database_url: str) -> Engine:
    options: dict[str, Any] = {"pool_pre_ping": True}
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        options.update(
            {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        )
    return create_engine(database_url, **options)


class GameRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self.engine = engine
        self.database_url = str(engine.url)
        if create_schema:
            GameBase.metadata.create_all(engine)

    @classmethod
    def from_environment(cls) -> "GameRepository | None":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            return None
        return cls(make_engine(database_url))

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(select(1))
            return True
        except SQLAlchemyError:
            return False
