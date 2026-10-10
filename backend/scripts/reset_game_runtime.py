"""Reset only local virtual game Runtime data; keep Workflow and catalog intact."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from backend.app.game.catalog import get_catalog
from backend.app.repositories.game_runtime import (
    ActionAttemptRow,
    ActionRecordRow,
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
from dotenv import load_dotenv
from sqlalchemy import delete

ROOT = Path(__file__).resolve().parents[2]


def reset() -> None:
    load_dotenv(ROOT / "backend" / ".env", override=False)
    database_url = os.getenv("DATABASE_URL", "").strip()
    repository = GameRepository.from_environment()
    if not database_url or repository is None:
        raise SystemExit("DATABASE_URL을 설정하세요.")
    catalog = get_catalog()
    demo_user = catalog["demo_user"]
    level = catalog["progression"]["start_level"]
    experience = (
        catalog["progression"]["level_thresholds"][level - 1]
        + catalog["progression"]["start_experience"]
    )
    now = datetime.now(UTC)
    with repository.session() as session, session.begin():
        session.execute(delete(EventParticipationRow))
        session.execute(delete(MissionProgressRow))
        session.execute(delete(RewardRecordRow))
        session.execute(delete(ShopPurchaseRow))
        session.execute(delete(InventoryLedgerRow))
        session.execute(delete(ActionRecordRow))
        session.execute(delete(ActionAttemptRow))
        session.execute(delete(GameRequestRow))
        session.execute(delete(GrowthRecordRow))
        session.execute(delete(RuntimeEventRow))
        user = session.get(VirtualUserRow, demo_user["id"])
        if user is not None:
            user.experience_total = experience
            user.highest_stage_order = 0
            user.current_hunt_stage_order = 1
            user.hunt_mode = "push"
            user.highest_dungeon_floor = 0
            user.current_dungeon_floor = 1
            user.dungeon_hunt_mode = "push"
            user.dungeon_success_count = 0
            user.equipped_skin_id = None
            user.weapon_upgrade_level = 0
            user.armor_upgrade_level = 0
            user.updated_at = now
            session.flush()
        session.execute(delete(SkinOwnershipRow))
        session.execute(
            delete(InventoryBalanceRow).where(InventoryBalanceRow.user_id == demo_user["id"])
        )
        for item_id, quantity in demo_user["starting_inventory"].items():
            session.add(
                InventoryBalanceRow(
                    user_id=demo_user["id"], item_id=item_id, quantity=quantity, updated_at=now
                )
            )
    print("가상 게임 진행·이벤트·행동·미션·보상 데이터만 초기화했습니다.")


if __name__ == "__main__":
    reset()
