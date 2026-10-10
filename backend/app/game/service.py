"""Transactional use cases for the server-authoritative game Runtime."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.game.catalog import get_catalog, mission_mapping
from backend.app.game.event_source import (
    EventSourceError,
    load_approved_event,
    preview_approved_event,
)
from backend.app.game.rules import (
    eligible,
    endless_dungeon_floor,
    endless_stage,
    event_day_number,
    event_status_at,
    level_for_experience,
    level_threshold,
    resolve_combat,
    resolve_party_combat,
)
from backend.app.game.schemas import (
    ApplyEventRequest,
    EquipSkinRequest,
    GameActionRequest,
    GameCommandType,
    GrowthActionRequest,
    ShopPurchaseRequest,
)
from backend.app.repositories.game_runtime import (
    ActionAttemptRow,
    ActionRecordRow,
    CatalogSnapshotRow,
    EventParticipationRow,
    GameRepository,
    GameRequestRow,
    GrowthRecordRow,
    InventoryBalanceRow,
    InventoryLedgerRow,
    MissionProgressRow,
    RewardRecordRow,
    RuntimeEventRow,
    ShopPurchaseRow,
    SkinOwnershipRow,
    VirtualUserRow,
)


class GameError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        detail: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or []


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _id(source: str) -> str:
    return str(uuid5(NAMESPACE_URL, source))


def event_status(event: RuntimeEventRow, now: datetime | None = None) -> str:
    return event_status_at(now or _now(), event.start_at, event.end_at)


def _event_day(event: RuntimeEventRow, now: datetime, timezone_name: str) -> int:
    return event_day_number(now, event.start_at, timezone_name)


def _level_for_experience(total: int, catalog: dict[str, Any]) -> int:
    progression = catalog["progression"]
    return level_for_experience(total, progression["level_thresholds"], progression["max_level"])


def _growth_cost(action_id: str, rank: int, catalog: dict[str, Any]) -> tuple[int, int]:
    action = catalog["growth_actions"][action_id]
    if rank <= len(action["costs"]):
        gold_cost = int(action["costs"][rank - 1])
    else:
        profile = catalog["endless_progression"]
        step_key = "weapon_cost_step" if action_id == "weapon_training" else "armor_cost_step"
        gold_cost = int(action["costs"][-1]) + int(profile[step_key]) * (
            rank - len(action["costs"])
        )
    shard_interval = int(catalog["endless_progression"]["moon_shard_rank_interval"])
    shard_cost = 1 if rank > len(action["costs"]) and rank % shard_interval == 0 else 0
    return gold_cost, shard_cost


def _user_view(
    user: VirtualUserRow,
    inventory: list[InventoryBalanceRow],
    catalog: dict[str, Any],
    owned_skins: list[SkinOwnershipRow] | None = None,
) -> dict[str, Any]:
    level = _level_for_experience(user.experience_total, catalog)
    thresholds = catalog["progression"]["level_thresholds"]
    previous_threshold = level_threshold(level, thresholds)
    next_threshold = level_threshold(level + 1, thresholds)
    stats = _hero_stats(user, level, catalog)
    current_stage = endless_stage(user.current_hunt_stage_order, catalog)
    current_dungeon = endless_dungeon_floor(user.current_dungeon_floor, catalog)
    growth_options = {}
    for action_id, action in catalog["growth_actions"].items():
        current_level = getattr(
            user, "weapon_upgrade_level" if action["stat"] == "attack" else "armor_upgrade_level"
        )
        next_cost, next_shard_cost = _growth_cost(action_id, current_level + 1, catalog)
        growth_options[action_id] = {
            **action,
            "level": current_level,
            "max_level": None,
            "next_cost": next_cost if next_cost <= 2**53 - 1 else None,
            "next_moon_shard_cost": next_shard_cost,
        }
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "segment_id": user.segment_id,
        "level": level,
        "experience_total": user.experience_total,
        "experience_in_level": user.experience_total - previous_threshold,
        "experience_to_next_level": next_threshold - previous_threshold,
        "highest_stage_order": user.highest_stage_order,
        "current_hunt_stage_order": user.current_hunt_stage_order,
        "current_hunt_stage_id": current_stage["id"] if current_stage else None,
        "hunt_mode": user.hunt_mode,
        "highest_dungeon_floor": user.highest_dungeon_floor,
        "current_dungeon_floor": user.current_dungeon_floor,
        "current_dungeon_floor_id": current_dungeon["id"],
        "dungeon_hunt_mode": user.dungeon_hunt_mode,
        "dungeon_success_count": user.dungeon_success_count,
        "equipped_skin_id": user.equipped_skin_id,
        "owned_skin_ids": [row.skin_id for row in (owned_skins or [])],
        "stats": stats,
        "growth_options": growth_options,
        "inventory": {row.item_id: row.quantity for row in inventory},
    }


def _hero_stats(user: VirtualUserRow, level: int, catalog: dict[str, Any]) -> dict[str, int]:
    profile = catalog["hero_stats"]
    return {
        "max_hp": profile["base_hp"]
        + (level - 1) * profile["hp_per_level"]
        + user.armor_upgrade_level * catalog["growth_actions"]["armor_training"]["amount"],
        "attack": profile["base_attack"]
        + (level - 1) * profile["attack_per_level"]
        + user.weapon_upgrade_level * catalog["growth_actions"]["weapon_training"]["amount"],
        "defense": profile["base_defense"],
    }


class GameService:
    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository
        source_catalog = get_catalog()
        try:
            with self.repository.session() as session:
                snapshot = session.get(CatalogSnapshotRow, source_catalog["game_data_snapshot_id"])
                if snapshot is None:
                    raise GameError(
                        "game_not_seeded",
                        "게임 카탈로그를 준비하세요: python -m backend.scripts.seed_game_runtime",
                        503,
                    )
                if snapshot.payload_digest != _hash(snapshot.catalog_payload):
                    raise GameError(
                        "catalog_corrupt", "저장된 게임 카탈로그 digest가 일치하지 않습니다.", 503
                    )
                self.catalog = snapshot.catalog_payload
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "게임 저장소에 연결할 수 없습니다.", 503
            ) from exc

    def _required_user(self, session: Session, *, lock: bool = False) -> VirtualUserRow:
        query = select(VirtualUserRow).where(
            VirtualUserRow.user_id == self.catalog["demo_user"]["id"]
        )
        if lock:
            query = query.with_for_update()
        user = session.scalar(query)
        if user is None:
            raise GameError(
                "game_not_seeded",
                "가상 게임 데이터를 준비하세요: python -m backend.scripts.seed_game_runtime",
                503,
            )
        return user

    def _user_view(self, session: Session, user: VirtualUserRow) -> dict[str, Any]:
        inventory = list(
            session.scalars(
                select(InventoryBalanceRow).where(InventoryBalanceRow.user_id == user.user_id)
            )
        )
        skins = list(
            session.scalars(
                select(SkinOwnershipRow).where(SkinOwnershipRow.user_id == user.user_id)
            )
        )
        return _user_view(user, inventory, self.catalog, skins)

    def state(self) -> dict[str, Any]:
        try:
            with self.repository.session() as session:
                user = self._required_user(session)
                events = list(
                    session.scalars(
                        select(RuntimeEventRow).order_by(RuntimeEventRow.applied_at.desc())
                    )
                )
                now = _now()
                return {
                    "game": {
                        "game_id": self.catalog["game_id"],
                        "display_name": self.catalog["display_name"],
                        "world_theme": self.catalog["world_theme"],
                    },
                    "versions": self._versions(),
                    "user": self._user_view(session, user),
                    "events": [self._event_view(event, now) for event in events],
                }
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "게임 저장소에 연결할 수 없습니다.", 503
            ) from exc

    def catalog_view(self, content_type: str) -> dict[str, Any]:
        collections = {
            "stages": self.catalog["stages"],
            "dungeons": self.catalog["dungeons"],
            "special_dungeon_templates": self.catalog["special_dungeon_templates"],
            "items": [
                {"item_id": item_id, **item} for item_id, item in self.catalog["items"].items()
            ],
        }
        if content_type not in collections:
            raise GameError("catalog_not_found", "요청한 카탈로그 유형이 없습니다.", 404)
        return {
            "content_type": content_type,
            "versions": self._versions(),
            "items": collections[content_type],
        }

    def content_at(self, content_type: str, order: int) -> dict[str, Any]:
        if order < 1 or order > 2**53 - 1:
            raise GameError(
                "content_order_out_of_range", "콘텐츠 순번이 지원 범위를 벗어났습니다.", 422
            )
        try:
            if content_type == "stages":
                content = endless_stage(order, self.catalog)
            elif content_type == "dungeons":
                content = endless_dungeon_floor(order, self.catalog)
            else:
                raise GameError("catalog_not_found", "요청한 콘텐츠 유형이 없습니다.", 404)
        except (OverflowError, ValueError) as exc:
            raise GameError(
                "content_value_out_of_range", "콘텐츠 수치를 계산할 수 없습니다.", 422
            ) from exc
        numeric_values = [
            content["experience_reward"],
            *[reward["quantity"] for reward in content["rewards"]],
            *[value for value in content["combat"].values() if isinstance(value, int)],
            *[
                value
                for enemy in content.get("enemies", [])
                for key, value in enemy.items()
                if key in {"hp", "attack", "defense", "position"} and isinstance(value, int)
            ],
        ]
        if any(value > 2**53 - 1 for value in numeric_values):
            raise GameError(
                "content_value_out_of_range", "콘텐츠 수치가 저장 범위를 벗어났습니다.", 422
            )
        return {"content_type": content_type, "versions": self._versions(), "item": content}

    def inventory(self, limit: int = 20) -> dict[str, Any]:
        with self.repository.session() as session:
            user = self._required_user(session)
            balances = list(
                session.scalars(
                    select(InventoryBalanceRow).where(InventoryBalanceRow.user_id == user.user_id)
                )
            )
            ownership = list(
                session.scalars(
                    select(SkinOwnershipRow).where(SkinOwnershipRow.user_id == user.user_id)
                )
            )
            ledger = list(
                session.scalars(
                    select(InventoryLedgerRow)
                    .where(InventoryLedgerRow.user_id == user.user_id)
                    .order_by(
                        InventoryLedgerRow.occurred_at.desc(), InventoryLedgerRow.entry_id.desc()
                    )
                    .limit(max(1, min(limit, 100)))
                )
            )
            owned_ids = {row.skin_id for row in ownership}
            skins = [
                {
                    "item_id": item_id,
                    **item,
                    "owned": item_id in owned_ids,
                    "equipped": item_id == user.equipped_skin_id,
                    "acquired_at": next(
                        (
                            row.acquired_at.isoformat()
                            for row in ownership
                            if row.skin_id == item_id
                        ),
                        None,
                    ),
                }
                for item_id, item in self.catalog["items"].items()
                if item.get("kind") == "skin"
            ]
            return {
                "user": self._user_view(session, user),
                "balances": {row.item_id: row.quantity for row in balances},
                "skins": skins,
                "equipped_skin_id": user.equipped_skin_id,
                "ledger": [self._ledger_view(row) for row in ledger],
                "ledger_limit": max(1, min(limit, 100)),
                "versions": self._versions(),
            }

    def _ledger_view(self, row: InventoryLedgerRow) -> dict[str, Any]:
        return {
            "entry_id": row.entry_id,
            "item_id": row.item_id,
            "item_name": self.catalog["items"].get(row.item_id, {}).get("name", row.item_id),
            "delta": row.delta,
            "balance_before": row.balance_before,
            "balance_after": row.balance_after,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "occurred_at": _aware(row.occurred_at).isoformat(),
        }

    def shop_offers(self) -> dict[str, Any]:
        with self.repository.session() as session:
            user = self._required_user(session)
            owned = {
                row.skin_id
                for row in session.scalars(
                    select(SkinOwnershipRow).where(SkinOwnershipRow.user_id == user.user_id)
                )
            }
            return {
                "offers": [
                    {
                        **offer,
                        "item": self.catalog["items"][offer["item_id"]],
                        "owned": offer["item_id"] in owned,
                        "equipped": offer["item_id"] == user.equipped_skin_id,
                    }
                    for offer in self.catalog["shop_offers"]
                ],
                "balances": self._user_view(session, user)["inventory"],
                "versions": self._versions(),
            }

    def activate_local_special_dungeon(self) -> dict[str, Any]:
        """Create or return a clearly labeled local-only event fixture for game play."""
        template = self.catalog["special_dungeon_templates"][0]
        source_task_id = f"LOCAL-FIXTURE-{self.catalog['game_data_snapshot_id']}"
        payload = {
            "event_name": "월광 균열 · 로컬 게임 fixture",
            "event_type": "special_dungeon",
            "target_user": "all_user",
            "venue": {"template_id": template["template_id"]},
            "missions": [],
            "rewards": [],
            "source": {"game_data_version": self.catalog["game_data_snapshot_id"]},
        }
        digest = _hash(payload)
        try:
            with self.repository.session() as session, session.begin():
                self._required_user(session, lock=True)
                existing = session.scalar(
                    select(RuntimeEventRow).where(
                        RuntimeEventRow.source_task_id == source_task_id,
                        RuntimeEventRow.result_version == 1,
                    )
                )
                if existing:
                    result = self._event_view(existing)
                    result["fixture"] = True
                    return result
                now = _now()
                event = RuntimeEventRow(
                    runtime_event_id=str(uuid5(NAMESPACE_URL, source_task_id)),
                    source_task_id=source_task_id,
                    result_version=1,
                    payload_digest=digest,
                    event_schema_version=self.catalog["event_schema_version"],
                    game_ruleset_version=self.catalog["game_ruleset_version"],
                    game_data_snapshot_id=self.catalog["game_data_snapshot_id"],
                    event_payload=payload,
                    start_at=datetime(2000, 1, 1, tzinfo=UTC),
                    end_at=datetime(2100, 1, 1, tzinfo=UTC),
                    applied_at=now,
                )
                session.add(event)
                session.flush()
                result = self._event_view(event, now)
                result["fixture"] = True
                return result
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "로컬 게임 fixture를 저장하지 못했습니다.", 503
            ) from exc

    def purchase_skin(self, request: ShopPurchaseRequest) -> dict[str, Any]:
        command = request.model_dump(mode="json")
        payload_hash = _hash(command)
        try:
            with self.repository.session() as session, session.begin():
                user = self._required_user(session, lock=True)
                prior = session.get(GameRequestRow, ("shop_purchase", str(request.request_id)))
                if prior:
                    if prior.payload_hash != payload_hash:
                        raise GameError(
                            "idempotency_key_conflict", "요청 ID가 다른 구매에 사용됐습니다.", 409
                        )
                    return prior.response_payload
                offer = next(
                    (
                        row
                        for row in self.catalog["shop_offers"]
                        if row["offer_id"] == request.offer_id
                    ),
                    None,
                )
                if offer is None:
                    raise GameError("shop_offer_not_found", "판매 중인 상품이 아닙니다.", 404)
                skin_id = offer["item_id"]
                if session.get(SkinOwnershipRow, (user.user_id, skin_id)):
                    raise GameError("skin_already_owned", "이미 보유한 스킨입니다.", 409)
                currency_id = offer["price_item_id"]
                balance = session.get(InventoryBalanceRow, (user.user_id, currency_id))
                available = balance.quantity if balance else 0
                price = int(offer["price"])
                if available < price:
                    raise GameError(
                        "insufficient_currency",
                        f"{self.catalog['items'][currency_id]['name']} {price}개가 필요합니다. (보유 {available}개)",
                        409,
                    )
                now = _now()
                purchase_id = str(uuid4())
                balance.quantity -= price
                balance.updated_at = now
                session.add(
                    SkinOwnershipRow(
                        user_id=user.user_id,
                        skin_id=skin_id,
                        source_type="shop_purchase",
                        source_id=purchase_id,
                        acquired_at=now,
                    )
                )
                session.flush()
                session.add(
                    ShopPurchaseRow(
                        purchase_id=purchase_id,
                        user_id=user.user_id,
                        request_id=str(request.request_id),
                        offer_id=offer["offer_id"],
                        skin_id=skin_id,
                        price_item_id=currency_id,
                        price=price,
                        created_at=now,
                    )
                )
                self._record_inventory_delta(
                    session,
                    user.user_id,
                    currency_id,
                    -price,
                    available,
                    available - price,
                    "shop_purchase",
                    purchase_id,
                    now,
                )
                response = {
                    "request_id": str(request.request_id),
                    "purchase_id": purchase_id,
                    "offer_id": offer["offer_id"],
                    "skin": {"item_id": skin_id, **self.catalog["items"][skin_id]},
                    "inventory_change": {
                        "item_id": currency_id,
                        "item_name": self.catalog["items"][currency_id]["name"],
                        "delta": -price,
                        "balance_before": available,
                        "balance_after": available - price,
                        "source_type": "shop_purchase",
                    },
                    "user": self._user_view(session, user),
                    "versions": self._versions(),
                }
                session.add(
                    GameRequestRow(
                        scope="shop_purchase",
                        request_id=str(request.request_id),
                        payload_hash=payload_hash,
                        response_payload=response,
                        created_at=now,
                    )
                )
                return response
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "스킨 구매를 저장하지 못했습니다.", 503
            ) from exc

    def equip_skin(self, request: EquipSkinRequest) -> dict[str, Any]:
        command = request.model_dump(mode="json")
        payload_hash = _hash(command)
        try:
            with self.repository.session() as session, session.begin():
                user = self._required_user(session, lock=True)
                prior = session.get(GameRequestRow, ("equip_skin", str(request.request_id)))
                if prior:
                    if prior.payload_hash != payload_hash:
                        raise GameError(
                            "idempotency_key_conflict",
                            "요청 ID가 다른 장착 변경에 사용됐습니다.",
                            409,
                        )
                    return prior.response_payload
                if request.skin_id is not None:
                    item = self.catalog["items"].get(request.skin_id)
                    if item is None or item.get("kind") != "skin":
                        raise GameError("skin_not_found", "카탈로그에 없는 스킨입니다.", 404)
                    if session.get(SkinOwnershipRow, (user.user_id, request.skin_id)) is None:
                        raise GameError("skin_not_owned", "보유한 스킨만 장착할 수 있습니다.", 409)
                user.equipped_skin_id = request.skin_id
                user.updated_at = _now()
                response = {
                    "equipped_skin_id": user.equipped_skin_id,
                    "user": self._user_view(session, user),
                    "versions": self._versions(),
                }
                session.add(
                    GameRequestRow(
                        scope="equip_skin",
                        request_id=str(request.request_id),
                        payload_hash=payload_hash,
                        response_payload=response,
                        created_at=user.updated_at,
                    )
                )
                return response
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "스킨 장착 상태를 저장하지 못했습니다.", 503
            ) from exc

    def _record_inventory_delta(
        self,
        session: Session,
        user_id: str,
        item_id: str,
        delta: int,
        before: int,
        after: int,
        source_type: str,
        source_id: str,
        now: datetime,
    ) -> None:
        session.add(
            InventoryLedgerRow(
                entry_id=_id(f"inventory:{source_type}:{source_id}:{item_id}"),
                user_id=user_id,
                item_id=item_id,
                delta=delta,
                balance_before=before,
                balance_after=after,
                source_type=source_type,
                source_id=source_id,
                occurred_at=now,
            )
        )

    def _versions(self) -> dict[str, str]:
        return {
            "domain_schema_version": self.catalog["domain_schema_version"],
            "event_schema_version": self.catalog["event_schema_version"],
            "game_ruleset_version": self.catalog["game_ruleset_version"],
            "game_data_snapshot_id": self.catalog["game_data_snapshot_id"],
        }

    def _event_view(
        self, event: RuntimeEventRow, current: datetime | None = None
    ) -> dict[str, Any]:
        payload = event.event_payload
        return {
            "runtime_event_id": event.runtime_event_id,
            "source_task_id": event.source_task_id,
            "result_version": event.result_version,
            "payload_digest": event.payload_digest,
            "event_name": payload["event_name"],
            "event_type": payload["event_type"],
            "target_user": payload["target_user"],
            "start_at": _aware(event.start_at).isoformat(),
            "end_at": _aware(event.end_at).isoformat(),
            "status": event_status(event, current),
            "fixture": event.source_task_id.startswith("LOCAL-FIXTURE-"),
            "venue": payload.get("venue"),
            "source_game_data_version": payload["source"].get("game_data_version"),
            "versions": self._versions_for_event(event),
        }

    def _versions_for_event(self, event: RuntimeEventRow) -> dict[str, str]:
        return {
            "event_schema_version": event.event_schema_version,
            "game_ruleset_version": event.game_ruleset_version,
            "game_data_snapshot_id": event.game_data_snapshot_id,
        }

    def apply_event(self, request: ApplyEventRequest) -> dict[str, Any]:
        try:
            source = load_approved_event(request.task_id, request.result_version)
        except EventSourceError as exc:
            raise GameError(exc.code, exc.message, exc.status_code) from exc
        source_payload = source["source"]
        result = source_payload["result"]
        source_game_data_version = source_payload["validation"].get("game_data_version")
        if source_game_data_version not in self.catalog.get(
            "planner_source_game_data_versions", []
        ):
            raise GameError(
                "catalog_version_unavailable",
                "기획안 검증에 사용된 게임 데이터 버전과 연결 매핑이 없습니다.",
                409,
            )
        payload_hash = _hash(
            {
                "task_id": request.task_id,
                "result_version": request.result_version,
                "payload_digest": source["digest"],
            }
        )
        try:
            with self.repository.session() as session, session.begin():
                user = self._required_user(session, lock=True)
                previous = session.get(GameRequestRow, ("event_apply", str(request.request_id)))
                if previous:
                    if previous.payload_hash != payload_hash:
                        raise GameError(
                            "idempotency_key_conflict",
                            "같은 요청 ID가 다른 내용에 사용됐습니다.",
                            409,
                        )
                    return previous.response_payload

                existing = session.scalar(
                    select(RuntimeEventRow).where(
                        RuntimeEventRow.source_task_id == request.task_id,
                        RuntimeEventRow.result_version == request.result_version,
                    )
                )
                if existing:
                    if existing.payload_digest != source["digest"]:
                        raise GameError(
                            "source_version_conflict",
                            "같은 승인 버전에 다른 결과 내용이 있습니다.",
                            409,
                        )
                    response = self._event_view(existing)
                    response["status"] = "applied"
                    response["event_status"] = event_status(existing)
                    response["idempotent_replay"] = True
                    session.add(
                        GameRequestRow(
                            scope="event_apply",
                            request_id=str(request.request_id),
                            payload_hash=payload_hash,
                            response_payload=response,
                            created_at=_now(),
                        )
                    )
                    return response

                payload = self._event_configuration(
                    result,
                    request.task_id,
                    request.result_version,
                    source_game_data_version,
                )
                if not self._eligible(user.segment_id, result["target_user"]):
                    raise GameError(
                        "not_eligible", "고정 데모 유저가 이 이벤트 대상에 포함되지 않습니다.", 422
                    )
                self._validate_reward_capacity(session, user.user_id, payload["rewards"])
                now = _now()
                event_id = str(uuid4())
                end_at = now + timedelta(days=result["duration"])
                event_row = RuntimeEventRow(
                    runtime_event_id=event_id,
                    source_task_id=request.task_id,
                    result_version=request.result_version,
                    payload_digest=source["digest"],
                    event_schema_version=self.catalog["event_schema_version"],
                    game_ruleset_version=self.catalog["game_ruleset_version"],
                    game_data_snapshot_id=self.catalog["game_data_snapshot_id"],
                    event_payload=payload,
                    start_at=now,
                    end_at=end_at,
                    applied_at=now,
                )
                session.add(event_row)
                session.flush()
                for mission in payload["missions"]:
                    session.add(
                        MissionProgressRow(
                            progress_id=str(uuid4()),
                            runtime_event_id=event_id,
                            mission_id=mission["mission_id"],
                            current_value=0,
                            target_value=mission["target"],
                            completed=False,
                            updated_at=now,
                        )
                    )
                response = self._event_view(event_row, now)
                response.update(
                    {"status": "applied", "event_status": "active", "idempotent_replay": False}
                )
                session.add(
                    GameRequestRow(
                        scope="event_apply",
                        request_id=str(request.request_id),
                        payload_hash=payload_hash,
                        response_payload=response,
                        created_at=now,
                    )
                )
                return response
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "이벤트 등록을 저장하지 못했습니다.", 503
            ) from exc

    def preview_event(self, task_id: str) -> dict[str, Any]:
        try:
            preview = preview_approved_event(task_id)
        except EventSourceError as exc:
            raise GameError(exc.code, exc.message, exc.status_code) from exc
        if preview["game_data_version"] not in self.catalog.get(
            "planner_source_game_data_versions", []
        ):
            raise GameError(
                "catalog_version_unavailable",
                "기획안 검증에 사용된 게임 데이터 버전과 연결 매핑이 없습니다.",
                409,
            )
        return preview

    def _eligible(self, segment_id: str, target: str) -> bool:
        return eligible(segment_id, target)

    def _event_configuration(
        self,
        result: dict[str, Any],
        task_id: str,
        version: int,
        source_game_data_version: str,
    ) -> dict[str, Any]:
        missions: list[dict[str, Any]] = []
        stage_content_ids = {row["id"] for row in self.catalog["stages"]}
        for index, source_mission in enumerate(result.get("missions") or [], start=1):
            try:
                mapped = mission_mapping(source_mission)
            except (TypeError, ValueError) as exc:
                raise GameError("unsupported_mission", str(exc), 422) from exc
            original_content = source_mission.get("content_id")
            if original_content:
                allowed = {
                    "monster_kill": {"HUNT_001"},
                    "dungeon_clear": {"DUNGEON_001", "dungeon_01"},
                    "stage_clear": {"HUNT_001", *stage_content_ids},
                    "level_reach": {"HUNT_001", *stage_content_ids},
                }.get(mapped["source_mission_type"], set())
                if original_content not in allowed:
                    raise GameError(
                        "unsupported_content",
                        f"게임 카탈로그에서 지원하지 않는 콘텐츠입니다: {original_content}",
                        422,
                    )
            mission_id = f"mission-{int(mapped['day'])}-{index}"
            missions.append({"mission_id": mission_id, **mapped})

        if not missions:
            raise GameError(
                "mission_required", "게임 이벤트에는 최소 한 개의 미션이 필요합니다.", 422
            )
        if len({mission["mission_id"] for mission in missions}) != len(missions):
            raise GameError("duplicate_mission", "이벤트 미션 식별자가 중복됐습니다.", 422)

        rewards: list[dict[str, Any]] = []
        for index, reward in enumerate(result.get("rewards") or [], start=1):
            item_id = str(reward.get("item_id", ""))
            item = self.catalog["items"].get(item_id)
            quantity = reward.get("quantity")
            if item is None:
                raise GameError(
                    "unknown_reward", f"지원하지 않는 보상 아이템입니다: {item_id}", 422
                )
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
                raise GameError("invalid_reward_quantity", "보상 수량은 양의 정수여야 합니다.", 422)
            day = int(reward.get("day", 1))
            mission = next((item for item in missions if item["day"] == day), None)
            if mission is None:
                raise GameError(
                    "reward_mission_missing", f"Day {day} 보상에 연결할 미션이 없습니다.", 422
                )
            if quantity > item["max_balance"]:
                raise GameError(
                    "reward_limit_exceeded", f"{item_id} 보상량이 보유 한도를 초과합니다.", 422
                )
            rewards.append(
                {
                    "reward_id": f"reward-{day}-{index}",
                    "day": day,
                    "mission_id": mission["mission_id"],
                    "item_id": item_id,
                    "item_name": item["name"],
                    "quantity": quantity,
                }
            )

        is_special = any(
            mission["action_type"] == "special_dungeon_progressed" for mission in missions
        )
        event_type = (
            "special_dungeon"
            if is_special
            else "dungeon_challenge"
            if any(m["action_type"] == "dungeon_cleared" for m in missions)
            else "growth_progression"
        )
        payload: dict[str, Any] = {
            "event_id": f"event-{task_id.lower()}-{version}",
            "event_schema_version": self.catalog["event_schema_version"],
            "event_type": event_type,
            "event_name": str(result.get("event_name") or "오퐁 히어로즈 이벤트"),
            "objective": str(result.get("objective") or ""),
            "target_user": str(result.get("target_user") or "all_user"),
            "duration": int(result.get("duration") or 0),
            "missions": missions,
            "rewards": rewards,
            "source": {
                "task_id": task_id,
                "result_version": version,
                "game_source": result.get("game_source", "opong"),
                "game_data_version": source_game_data_version,
            },
        }
        if not 1 <= payload["duration"] <= 7:
            raise GameError("invalid_event_period", "이벤트 기간은 1~7일이어야 합니다.", 422)
        if is_special:
            template = self.catalog["special_dungeon_templates"][0]
            payload["venue"] = {
                "template_id": template["template_id"],
                "name": template["name"],
                "theme_id": template["theme_id"],
                "layout_id": template["layout_id"],
                "difficulty_id": template["difficulty_id"],
            }
            payload["daily_attempt_limit"] = template["daily_attempt_limit"]
        return payload

    def _validate_reward_capacity(
        self, session: Session, user_id: str, rewards: list[dict[str, Any]]
    ) -> None:
        totals: dict[str, int] = {}
        for reward in rewards:
            totals[reward["item_id"]] = totals.get(reward["item_id"], 0) + reward["quantity"]
        for item_id, increment in totals.items():
            balance = session.get(InventoryBalanceRow, (user_id, item_id))
            current = balance.quantity if balance else 0
            if current + increment > self.catalog["items"][item_id]["max_balance"]:
                raise GameError(
                    "reward_limit_exceeded", f"{item_id} 예상 지급량이 보유 한도를 초과합니다.", 422
                )

    def event(self, event_id: str) -> dict[str, Any]:
        with self.repository.session() as session:
            event = session.get(RuntimeEventRow, event_id)
            if event is None:
                raise GameError("event_not_found", "적용된 게임 이벤트를 찾을 수 없습니다.", 404)
            return {**self._event_view(event), "configuration": event.event_payload}

    def progress(self, event_id: str) -> dict[str, Any]:
        with self.repository.session() as session:
            event = session.get(RuntimeEventRow, event_id)
            if event is None:
                raise GameError("event_not_found", "적용된 게임 이벤트를 찾을 수 없습니다.", 404)
            rows = list(
                session.scalars(
                    select(MissionProgressRow)
                    .where(MissionProgressRow.runtime_event_id == event_id)
                    .order_by(MissionProgressRow.mission_id)
                )
            )
            rewards = list(
                session.scalars(
                    select(RewardRecordRow)
                    .where(RewardRecordRow.runtime_event_id == event_id)
                    .order_by(RewardRecordRow.created_at)
                )
            )
            attempts = list(
                session.scalars(
                    select(ActionAttemptRow)
                    .where(ActionAttemptRow.runtime_event_id == event_id)
                    .order_by(ActionAttemptRow.occurred_at.desc())
                    .limit(20)
                )
            )
            actions = list(
                session.scalars(
                    select(ActionRecordRow)
                    .where(ActionRecordRow.runtime_event_id == event_id)
                    .order_by(ActionRecordRow.occurred_at.desc())
                    .limit(30)
                )
            )
            user = self._required_user(session)
            current_day = _event_day(event, _now(), self.catalog["game_day_timezone"])
            status = event_status(event)
            payload_missions = {
                mission["mission_id"]: mission for mission in event.event_payload["missions"]
            }
            return {
                "event": self._event_view(event),
                "user": self._user_view(session, user),
                "missions": [
                    {
                        **payload_missions[row.mission_id],
                        "current_value": row.current_value,
                        "completed": row.completed,
                        "status": (
                            "completed"
                            if row.completed
                            else "upcoming"
                            if payload_missions[row.mission_id]["day"] > current_day
                            else "active"
                            if payload_missions[row.mission_id]["day"] == current_day
                            and status == "active"
                            else "missed"
                        ),
                        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    }
                    for row in rows
                ],
                "rewards": [
                    {
                        "reward_record_id": row.reward_record_id,
                        "mission_id": row.mission_id,
                        "item_id": row.item_id,
                        "item_name": self.catalog["items"]
                        .get(row.item_id, {})
                        .get("name", row.item_id),
                        "quantity": row.quantity,
                        "created_at": _aware(row.created_at).isoformat(),
                    }
                    for row in rewards
                ],
                "attempts": [
                    {
                        "attempt_id": row.attempt_id,
                        "request_id": row.request_id,
                        "command_type": row.command_type,
                        "content_id": row.content_id,
                        "result": row.result,
                        "reason_code": row.reason_code,
                        "occurred_at": _aware(row.occurred_at).isoformat(),
                    }
                    for row in attempts
                ],
                "actions": [
                    {
                        "action_id": row.action_id,
                        "attempt_id": row.attempt_id,
                        "action_type": row.action_type,
                        "subtype": row.subtype,
                        "content_id": row.content_id,
                        "occurred_at": _aware(row.occurred_at).isoformat(),
                    }
                    for row in actions
                ],
            }

    def command_result(self, request_id: str) -> dict[str, Any]:
        with self.repository.session() as session:
            rows = list(
                session.scalars(
                    select(GameRequestRow).where(GameRequestRow.request_id == request_id)
                )
            )
            if not rows:
                raise GameError("command_not_found", "요청 결과가 없습니다.", 404)
            return rows[0].response_payload

    def action(self, request: GameActionRequest) -> dict[str, Any]:
        command_data = request.model_dump(mode="json")
        payload_hash = _hash(command_data)
        try:
            with self.repository.session() as session, session.begin():
                user = self._required_user(session, lock=True)
                prior = session.get(GameRequestRow, ("action", str(request.request_id)))
                if prior:
                    if prior.payload_hash != payload_hash:
                        raise GameError(
                            "idempotency_key_conflict",
                            "같은 요청 ID가 다른 행동에 사용됐습니다.",
                            409,
                        )
                    return prior.response_payload
                now = _now()
                level_before = _level_for_experience(user.experience_total, self.catalog)
                event = None
                event_id = str(request.runtime_event_id) if request.runtime_event_id else None
                event_payload = None
                if event_id:
                    event = session.get(RuntimeEventRow, event_id)
                    if event is None:
                        raise GameError("event_not_found", "요청한 게임 이벤트가 없습니다.", 404)
                    if event_status(event, now) != "active":
                        raise GameError(
                            "event_not_active", "현재 플레이할 수 있는 이벤트가 아닙니다.", 409
                        )
                    event_payload = event.event_payload
                    self._ensure_participation(session, event, user, now)

                content = self._resolve_content(session, request, user, event_payload, now)
                attempt_id = str(uuid4())
                try:
                    combat = self._combat(user, content, level_before, request.command_type)
                except (OverflowError, ValueError) as exc:
                    raise GameError(
                        "combat_value_out_of_range",
                        "전투 입력이 규칙 계산 범위를 벗어났습니다.",
                        422,
                    ) from exc
                succeeded = combat["result"] == "succeeded"
                reason_code = "cleared" if succeeded else "hero_hp_depleted"
                attempt = ActionAttemptRow(
                    attempt_id=attempt_id,
                    request_id=str(request.request_id),
                    user_id=user.user_id,
                    runtime_event_id=event_id,
                    command_type=request.command_type.value,
                    content_id=request.content_id,
                    result="succeeded" if succeeded else "failed",
                    reason_code=reason_code,
                    occurred_at=now,
                    attempt_payload={**command_data, "level_at_attempt": level_before, **combat},
                )
                session.add(attempt)
                session.flush()

                actions: list[dict[str, Any]] = []
                granted: list[dict[str, Any]] = []
                mission_changes: list[dict[str, Any]] = []
                level_after = level_before
                if succeeded:
                    primary_action = self._action_type(request.command_type)
                    payload = self._action_payload(request, content)
                    payload["combat"] = combat
                    if request.command_type == GameCommandType.SPECIAL_DUNGEON_CHALLENGE:
                        payload["progress_type"] = "floor_cleared"
                        payload["monster_count"] = int(content.get("monster_count", 0))
                    primary = self._record_action(
                        session,
                        attempt,
                        user,
                        event_id,
                        primary_action,
                        "floor_cleared"
                        if primary_action == "special_dungeon_progressed"
                        else "default",
                        now,
                        payload,
                    )
                    actions.append(primary)
                    if (
                        request.command_type == GameCommandType.SPECIAL_DUNGEON_CHALLENGE
                        and content.get("is_boss")
                    ):
                        boss_payload = {
                            **payload,
                            "progress_type": "boss_defeated",
                            "boss_id": content["id"],
                        }
                        actions.append(
                            self._record_action(
                                session,
                                attempt,
                                user,
                                event_id,
                                "special_dungeon_progressed",
                                "boss_defeated",
                                now,
                                boss_payload,
                            )
                        )
                    xp_gain = int(content.get("experience_reward", 0))
                    if user.experience_total + xp_gain > 2**53 - 1:
                        raise GameError(
                            "progression_value_out_of_range",
                            "경험치가 안전한 정수 범위를 벗어났습니다.",
                            409,
                        )
                    user.experience_total += xp_gain
                    if request.command_type == GameCommandType.STAGE_CHALLENGE:
                        previous_highest = user.highest_stage_order
                        user.highest_stage_order = max(user.highest_stage_order, content["order"])
                        if content["order"] > previous_highest:
                            milestone = next(
                                (
                                    row
                                    for row in self.catalog["cosmetic_milestones"]
                                    if row["stage_order"] == content["order"]
                                ),
                                None,
                            )
                            if milestone:
                                granted.append(
                                    self._grant(
                                        session,
                                        user.user_id,
                                        milestone["item_id"],
                                        milestone["quantity"],
                                        "stage_milestone",
                                        f"stage:{content['order']}",
                                        None,
                                        None,
                                        now,
                                    )
                                )
                    elif request.command_type == GameCommandType.DUNGEON_CHALLENGE:
                        user.highest_dungeon_floor = max(
                            user.highest_dungeon_floor, content["order"]
                        )
                        user.dungeon_success_count += 1
                        shard_interval = int(
                            self.catalog["endless_progression"]["dungeon_shard_every_clears"]
                        )
                        if user.dungeon_success_count % shard_interval == 0:
                            granted.append(
                                self._grant(
                                    session,
                                    user.user_id,
                                    "MOON_SHARD",
                                    1,
                                    "dungeon_clear_milestone",
                                    f"clear-count:{user.dungeon_success_count}",
                                    None,
                                    None,
                                    now,
                                )
                            )
                    level_after = _level_for_experience(user.experience_total, self.catalog)
                    for milestone_level in range(level_before + 1, level_after + 1):
                        milestone_payload = {
                            "before_level": level_before,
                            "after_level": milestone_level,
                            "hero_level": milestone_level,
                            "experience_delta": xp_gain,
                        }
                        actions.append(
                            self._record_action(
                                session,
                                attempt,
                                user,
                                event_id,
                                "growth_milestone_reached",
                                f"level_{milestone_level}",
                                now,
                                milestone_payload,
                            )
                        )
                    for reward in content.get("rewards", []):
                        granted.append(
                            self._grant(
                                session,
                                user.user_id,
                                reward["item_id"],
                                reward["quantity"],
                                "content_clear",
                                attempt_id,
                                None,
                                None,
                                now,
                            )
                        )
                    if event and event_payload:
                        for action in actions:
                            action_changes = self._advance_missions(
                                session, event, action, user, now
                            )
                            mission_changes.extend(action_changes)
                            for change in action_changes:
                                if change.get("just_completed"):
                                    for event_reward in event_payload["rewards"]:
                                        if event_reward["mission_id"] == change["mission_id"]:
                                            granted.append(
                                                self._grant(
                                                    session,
                                                    user.user_id,
                                                    event_reward["item_id"],
                                                    event_reward["quantity"],
                                                    "event_mission",
                                                    f"{event_id}:{change['mission_id']}:{event_reward['reward_id']}",
                                                    event_id,
                                                    change["mission_id"],
                                                    now,
                                                )
                                            )
                stage_progress = None
                if request.command_type == GameCommandType.STAGE_CHALLENGE:
                    mode = content.get("resolved_hunt_mode", request.stage_mode or user.hunt_mode)
                    if mode == "push" and succeeded:
                        user.current_hunt_stage_order = content["order"] + 1
                        user.hunt_mode = "push"
                    elif mode == "push":
                        user.current_hunt_stage_order = max(1, user.highest_stage_order)
                        user.hunt_mode = "farm"
                    else:
                        user.current_hunt_stage_order = max(1, user.highest_stage_order)
                        user.hunt_mode = "farm"
                    next_stage = endless_stage(user.current_hunt_stage_order, self.catalog)
                    continue_auto = (
                        succeeded
                        and mode == "push"
                        and user.hunt_mode == "push"
                        or mode == "farm"
                        or (not succeeded and mode == "push")
                    )
                    stage_progress = {
                        "result": combat["result"],
                        "highest_cleared_stage_order": user.highest_stage_order,
                        "hunt_mode": user.hunt_mode,
                        "next_stage_id": next_stage["id"] if next_stage else None,
                        "next_action": "continue_automatically"
                        if continue_auto
                        else "await_player",
                    }
                dungeon_progress = None
                if request.command_type == GameCommandType.DUNGEON_CHALLENGE:
                    mode = content.get(
                        "resolved_dungeon_mode", request.dungeon_mode or user.dungeon_hunt_mode
                    )
                    if succeeded:
                        user.highest_dungeon_floor = max(
                            user.highest_dungeon_floor, content["order"]
                        )
                    if mode == "push" and succeeded:
                        user.current_dungeon_floor = content["order"] + 1
                        user.dungeon_hunt_mode = "push"
                    elif mode == "push":
                        user.current_dungeon_floor = max(1, user.highest_dungeon_floor)
                        user.dungeon_hunt_mode = "farm"
                    else:
                        user.current_dungeon_floor = max(1, user.highest_dungeon_floor)
                        user.dungeon_hunt_mode = "farm"
                    dungeon_progress = {
                        "result": combat["result"],
                        "highest_cleared_floor": user.highest_dungeon_floor,
                        "current_floor": user.current_dungeon_floor,
                        "hunt_mode": user.dungeon_hunt_mode,
                        "next_floor_id": endless_dungeon_floor(
                            user.current_dungeon_floor, self.catalog
                        )["id"],
                        "next_action": "continue_automatically",
                    }
                user.updated_at = now
                session.flush()
                response = {
                    "request_id": str(request.request_id),
                    "attempt": {
                        "attempt_id": attempt_id,
                        "result": attempt.result,
                        "reason_code": reason_code,
                        "occurred_at": now.isoformat(),
                        "command_type": request.command_type.value,
                        "content_id": request.content_id,
                    },
                    "actions": actions,
                    "user": self._user_view(session, user),
                    "level_up": level_after > level_before,
                    "experience_gained": int(content.get("experience_reward", 0))
                    if succeeded
                    else 0,
                    "mission_changes": mission_changes,
                    "reward_records": granted,
                    "inventory_changes": granted,
                    "combat": combat,
                    "stage_progress": stage_progress,
                    "dungeon_progress": dungeon_progress,
                    "versions": self._versions(),
                }
                session.add(
                    GameRequestRow(
                        scope="action",
                        request_id=str(request.request_id),
                        payload_hash=payload_hash,
                        response_payload=response,
                        created_at=now,
                    )
                )
                return response
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "게임 행동을 저장하지 못했습니다.", 503
            ) from exc

    def _combat(
        self,
        user: VirtualUserRow,
        content: dict[str, Any],
        level: int,
        command_type: GameCommandType,
    ) -> dict[str, Any]:
        hero = _hero_stats(user, level, self.catalog)
        if command_type == GameCommandType.DUNGEON_CHALLENGE:
            combat = resolve_party_combat(hero, content["enemies"])
            combat["encounter_snapshot"] = [dict(enemy) for enemy in content["enemies"]]
            combat["game_ruleset_version"] = self.catalog["game_ruleset_version"]
            combat["game_data_snapshot_id"] = self.catalog["game_data_snapshot_id"]
            return combat
        monster_profile = content.get("combat")
        if not monster_profile:
            # Old snapshots remain readable while all new challenge data carries a profile.
            monster_profile = {"monster_hp": 1, "monster_attack": 1, "monster_defense": 0}
        monster = {
            "max_hp": int(monster_profile["monster_hp"]),
            "attack": int(monster_profile["monster_attack"]),
            "defense": int(monster_profile.get("monster_defense", 0)),
        }
        return resolve_combat(hero, monster)

    def grow(self, request: GrowthActionRequest) -> dict[str, Any]:
        command_data = request.model_dump(mode="json")
        payload_hash = _hash(command_data)
        try:
            with self.repository.session() as session, session.begin():
                user = self._required_user(session, lock=True)
                prior = session.get(GameRequestRow, ("growth", str(request.request_id)))
                if prior:
                    if prior.payload_hash != payload_hash:
                        raise GameError(
                            "idempotency_key_conflict",
                            "같은 요청 ID가 다른 성장 행동에 사용됐습니다.",
                            409,
                        )
                    return prior.response_payload
                action = self.catalog["growth_actions"].get(request.growth_action_id)
                if action is None:
                    raise GameError(
                        "growth_action_not_found", "지원하지 않는 성장 행동입니다.", 404
                    )
                if request.growth_action_id not in {"weapon_training", "armor_training"}:
                    raise GameError(
                        "growth_action_not_found", "지원하지 않는 성장 행동입니다.", 404
                    )
                field = (
                    "weapon_upgrade_level" if action["stat"] == "attack" else "armor_upgrade_level"
                )
                current_level = int(getattr(user, field))
                rank = current_level + 1
                gold_cost, shard_cost = _growth_cost(request.growth_action_id, rank, self.catalog)
                if rank > 2**53 - 1 or gold_cost > 2**53 - 1:
                    raise GameError(
                        "growth_value_out_of_range",
                        "강화 수치가 안전한 정수 범위를 벗어났습니다.",
                        409,
                    )
                costs = {action["item_id"]: gold_cost}
                if shard_cost:
                    costs["MOON_SHARD"] = shard_cost
                balances: dict[str, tuple[InventoryBalanceRow | None, int]] = {}
                for item_id, cost in costs.items():
                    balance_row = session.get(InventoryBalanceRow, (user.user_id, item_id))
                    available = balance_row.quantity if balance_row else 0
                    if available < cost:
                        item_name = self.catalog["items"][item_id]["name"]
                        raise GameError(
                            "insufficient_currency",
                            f"강화에 {item_name} {cost}개가 필요합니다. (보유 {available}개)",
                            409,
                        )
                    balances[item_id] = (balance_row, available)
                level = _level_for_experience(user.experience_total, self.catalog)
                before_stats = _hero_stats(user, level, self.catalog)
                now = _now()
                inventory_changes = []
                for item_id, cost in costs.items():
                    balance_row, available = balances[item_id]
                    if balance_row is None:
                        raise GameError(
                            "inventory_unavailable", "재화 보유 상태를 찾을 수 없습니다.", 503
                        )
                    balance_row.quantity -= cost
                    balance_row.updated_at = now
                    self._record_inventory_delta(
                        session,
                        user.user_id,
                        item_id,
                        -cost,
                        available,
                        available - cost,
                        "growth",
                        str(request.request_id),
                        now,
                    )
                    inventory_changes.append(
                        {
                            "item_id": item_id,
                            "item_name": self.catalog["items"][item_id]["name"],
                            "quantity": cost,
                            "delta": -cost,
                            "balance_before": available,
                            "balance_after": available - cost,
                            "source_type": "growth",
                        }
                    )
                setattr(user, field, current_level + 1)
                user.updated_at = now
                after_stats = _hero_stats(user, level, self.catalog)
                if any(value > 2**53 - 1 for value in after_stats.values()):
                    raise GameError(
                        "growth_value_out_of_range",
                        "강화 후 능력치가 안전한 정수 범위를 벗어납니다.",
                        409,
                    )
                record_id = str(uuid4())
                session.add(
                    GrowthRecordRow(
                        growth_record_id=record_id,
                        request_id=str(request.request_id),
                        user_id=user.user_id,
                        growth_action_id=request.growth_action_id,
                        item_id=action["item_id"],
                        quantity_spent=cost,
                        upgrade_level_before=current_level,
                        upgrade_level_after=current_level + 1,
                        before_stats=before_stats,
                        after_stats=after_stats,
                        created_at=now,
                    )
                )
                response = {
                    "request_id": str(request.request_id),
                    "growth_record_id": record_id,
                    "growth_action_id": request.growth_action_id,
                    "name": action["name"],
                    "item_id": action["item_id"],
                    "quantity_spent": gold_cost,
                    "inventory_changes": inventory_changes,
                    "stats_before": before_stats,
                    "stats_after": after_stats,
                    "user": self._user_view(session, user),
                    "versions": self._versions(),
                }
                session.add(
                    GameRequestRow(
                        scope="growth",
                        request_id=str(request.request_id),
                        payload_hash=payload_hash,
                        response_payload=response,
                        created_at=now,
                    )
                )
                return response
        except GameError:
            raise
        except SQLAlchemyError as exc:
            raise GameError(
                "runtime_storage_unavailable", "성장 결과를 저장하지 못했습니다.", 503
            ) from exc

    def _ensure_participation(
        self, session: Session, event: RuntimeEventRow, user: VirtualUserRow, now: datetime
    ) -> None:
        existing = session.scalar(
            select(EventParticipationRow).where(
                EventParticipationRow.runtime_event_id == event.runtime_event_id,
                EventParticipationRow.user_id == user.user_id,
            )
        )
        if existing:
            return
        target = event.event_payload["target_user"]
        if not self._eligible(user.segment_id, target):
            raise GameError("not_eligible", "이 이벤트 대상 유저가 아닙니다.", 403)
        session.add(
            EventParticipationRow(
                participation_id=str(uuid4()),
                runtime_event_id=event.runtime_event_id,
                user_id=user.user_id,
                eligibility_snapshot={"segment_id": user.segment_id, "target_user": target},
                created_at=now,
            )
        )
        session.flush()

    def _resolve_content(
        self,
        session: Session,
        request: GameActionRequest,
        user: VirtualUserRow,
        event_payload: dict[str, Any] | None,
        now: datetime,
    ) -> dict[str, Any]:
        level = _level_for_experience(user.experience_total, self.catalog)
        if request.command_type == GameCommandType.STAGE_CHALLENGE:
            mode = request.stage_mode or user.hunt_mode
            expected_order = (
                user.highest_stage_order + 1 if mode == "push" else max(1, user.highest_stage_order)
            )
            content = endless_stage(expected_order, self.catalog)
            if content["id"] != request.content_id:
                raise GameError("content_not_found", "요청한 스테이지가 없습니다.", 404)
            if level < content["unlock_level"]:
                raise GameError(
                    "content_locked",
                    f"현재 {mode} 사냥 대상은 {expected_order} 스테이지입니다.",
                    409,
                )
            checked = self.content_at("stages", expected_order)["item"]
            return {**checked, "resolved_hunt_mode": mode}
        if request.command_type == GameCommandType.DUNGEON_CHALLENGE:
            mode = request.dungeon_mode or user.dungeon_hunt_mode
            expected_order = (
                user.highest_dungeon_floor + 1
                if mode == "push"
                else max(1, user.highest_dungeon_floor)
            )
            content = endless_dungeon_floor(expected_order, self.catalog)
            if content["id"] != request.content_id:
                raise GameError("content_not_found", "요청한 던전이 없습니다.", 404)
            checked = self.content_at("dungeons", expected_order)["item"]
            return {**checked, "resolved_dungeon_mode": mode}
        if not event_payload or not event_payload.get("venue"):
            raise GameError(
                "event_required", "이벤트에 등록된 특별 던전만 도전할 수 있습니다.", 409
            )
        template = next(
            (
                row
                for row in self.catalog["special_dungeon_templates"]
                if row["template_id"] == event_payload["venue"]["template_id"]
            ),
            None,
        )
        if template is None:
            raise GameError(
                "catalog_version_unavailable", "특별 던전 템플릿 버전을 찾을 수 없습니다.", 409
            )
        floors = template["floors"]
        completed_actions = session.scalars(
            select(ActionRecordRow).where(
                ActionRecordRow.runtime_event_id == str(request.runtime_event_id),
                ActionRecordRow.action_type == "special_dungeon_progressed",
            )
        )
        done_ids = {
            row.action_payload.get("floor_id")
            for row in completed_actions
            if row.action_payload.get("progress_type") == "floor_cleared"
        }
        next_floor = next((floor for floor in floors if floor["id"] not in done_ids), None)
        if next_floor is None or next_floor["id"] != request.content_id:
            raise GameError(
                "content_locked", "특별 던전은 현재 진행 중인 층만 도전할 수 있습니다.", 409
            )
        if level < next_floor["entry_level"]:
            raise GameError("content_locked", "특별 던전 입장 레벨을 달성하지 못했습니다.", 409)
        self._check_daily_limit(
            session,
            user.user_id,
            request.command_type.value,
            request.content_id,
            template["daily_attempt_limit"],
            now,
            event_id=str(request.runtime_event_id),
        )
        return next_floor

    def _check_daily_limit(
        self,
        session: Session,
        user_id: str,
        command: str,
        content_id: str,
        limit: int,
        now: datetime,
        event_id: str | None = None,
    ) -> None:
        timezone = ZoneInfo(self.catalog["game_day_timezone"])
        local_day = now.astimezone(timezone).date()
        reset = time.fromisoformat(self.catalog["daily_reset_time"])
        start_local = datetime.combine(local_day, reset, tzinfo=timezone)
        start_utc = start_local.astimezone(UTC)
        query = (
            select(func.count())
            .select_from(ActionAttemptRow)
            .where(
                ActionAttemptRow.user_id == user_id,
                ActionAttemptRow.command_type == command,
                ActionAttemptRow.content_id == content_id,
                ActionAttemptRow.occurred_at >= start_utc,
            )
        )
        if event_id:
            query = query.where(ActionAttemptRow.runtime_event_id == event_id)
        if session.scalar(query) >= limit:
            raise GameError("attempt_limit_reached", "오늘의 도전 횟수를 모두 사용했습니다.", 409)

    def _action_type(self, command: GameCommandType) -> str:
        return {
            GameCommandType.STAGE_CHALLENGE: "stage_cleared",
            GameCommandType.DUNGEON_CHALLENGE: "dungeon_cleared",
            GameCommandType.SPECIAL_DUNGEON_CHALLENGE: "special_dungeon_progressed",
        }[command]

    def _action_payload(
        self, request: GameActionRequest, content: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {"name": content["name"], "experience_reward": content["experience_reward"]}
        if request.command_type == GameCommandType.STAGE_CHALLENGE:
            payload.update({"stage_id": content["id"], "stage_order": content["order"]})
        elif request.command_type == GameCommandType.DUNGEON_CHALLENGE:
            payload.update(
                {
                    "dungeon_id": "dungeon_01",
                    "floor_id": content["id"],
                    "floor_index": content["order"],
                }
            )
        else:
            payload.update({"floor_id": content["id"], "floor_index": content["order"]})
        return payload

    def _record_action(
        self,
        session: Session,
        attempt: ActionAttemptRow,
        user: VirtualUserRow,
        event_id: str | None,
        action_type: str,
        subtype: str,
        now: datetime,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = _id(f"{attempt.attempt_id}:{action_type}:{subtype}")
        action = ActionRecordRow(
            action_id=action_id,
            attempt_id=attempt.attempt_id,
            user_id=user.user_id,
            runtime_event_id=event_id,
            action_type=action_type,
            subtype=subtype,
            content_id=attempt.content_id,
            game_ruleset_version=self.catalog["game_ruleset_version"],
            game_data_snapshot_id=self.catalog["game_data_snapshot_id"],
            occurred_at=now,
            action_payload=payload,
        )
        session.add(action)
        session.flush()
        return {
            "action_id": action_id,
            "action_type": action_type,
            "subtype": subtype,
            "content_id": attempt.content_id,
            **payload,
        }

    def _advance_missions(
        self,
        session: Session,
        event: RuntimeEventRow,
        action: dict[str, Any],
        user: VirtualUserRow,
        now: datetime,
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        current_day = _event_day(event, now, self.catalog["game_day_timezone"])
        for mission in event.event_payload["missions"]:
            if mission["day"] != current_day:
                continue
            if mission["action_type"] != action["action_type"]:
                continue
            if mission.get("progress_type") and mission["progress_type"] != action.get(
                "progress_type"
            ):
                continue
            source_content_id = mission.get("source_content_id")
            generic_content_ids = {
                "stage_cleared": {"HUNT_001"},
                "dungeon_cleared": {"DUNGEON_001"},
                "special_dungeon_progressed": {"HUNT_001"},
                "growth_milestone_reached": {"HUNT_001"},
            }.get(action["action_type"], set())
            actual_content_ids = {
                action.get("stage_id"),
                action.get("dungeon_id"),
                action.get("floor_id"),
                action.get("content_id"),
            }
            if (
                source_content_id
                and source_content_id not in generic_content_ids | actual_content_ids
            ):
                continue
            row = session.scalar(
                select(MissionProgressRow)
                .where(
                    MissionProgressRow.runtime_event_id == event.runtime_event_id,
                    MissionProgressRow.mission_id == mission["mission_id"],
                )
                .with_for_update()
            )
            if row is None or row.completed:
                continue
            if mission["metric"] == "hero_level":
                if action.get("action_type") != "growth_milestone_reached":
                    continue
                row.current_value = max(row.current_value, int(action.get("hero_level", 0)))
            else:
                increment = (
                    int(action.get("monster_count", 1))
                    if mission["source_mission_type"] == "monster_kill"
                    else 1
                )
                row.current_value = min(row.target_value, row.current_value + increment)
            just_completed = row.current_value >= row.target_value
            if just_completed:
                row.completed = True
                row.completed_at = now
            row.updated_at = now
            changes.append(
                {
                    "mission_id": mission["mission_id"],
                    "name": mission["name"],
                    "current_value": row.current_value,
                    "target_value": row.target_value,
                    "completed": row.completed,
                    "just_completed": just_completed,
                }
            )
        return changes

    def _grant(
        self,
        session: Session,
        user_id: str,
        item_id: str,
        quantity: int,
        source_type: str,
        source_id: str,
        event_id: str | None,
        mission_id: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        item = self.catalog["items"].get(item_id)
        if item is None or quantity < 1:
            raise GameError("invalid_reward", "카탈로그에 없는 보상입니다.", 422)
        if item.get("kind") == "skin":
            if quantity != 1 or session.get(SkinOwnershipRow, (user_id, item_id)):
                raise GameError(
                    "reward_limit_exceeded", f"{item_id}는 한 번만 보유할 수 있습니다.", 409
                )
            session.add(
                SkinOwnershipRow(
                    user_id=user_id,
                    skin_id=item_id,
                    source_type=source_type,
                    source_id=source_id,
                    acquired_at=now,
                )
            )
            balance_before = balance_after = None
        else:
            balance = session.get(InventoryBalanceRow, (user_id, item_id))
            balance_before = balance.quantity if balance else 0
            balance_after = balance_before + quantity
            if balance_after > item["max_balance"]:
                raise GameError("reward_limit_exceeded", f"{item_id} 보유 한도를 초과합니다.", 409)
            if balance:
                balance.quantity = balance_after
                balance.updated_at = now
            else:
                session.add(
                    InventoryBalanceRow(
                        user_id=user_id, item_id=item_id, quantity=balance_after, updated_at=now
                    )
                )
            self._record_inventory_delta(
                session,
                user_id,
                item_id,
                quantity,
                balance_before,
                balance_after,
                source_type,
                source_id,
                now,
            )
        record_id = _id(f"{source_type}:{source_id}:{item_id}")
        session.add(
            RewardRecordRow(
                reward_record_id=record_id,
                user_id=user_id,
                runtime_event_id=event_id,
                mission_id=mission_id,
                item_id=item_id,
                quantity=quantity,
                grant_source_type=source_type,
                source_id=source_id,
                created_at=now,
            )
        )
        return {
            "reward_record_id": record_id,
            "item_id": item_id,
            "item_name": item["name"],
            "quantity": quantity,
            "delta": quantity,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "source_type": source_type,
        }
