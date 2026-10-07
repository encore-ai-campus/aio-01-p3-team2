"""Backend A environment settings without secret values."""

import os


def approved_actor_ids() -> set[str]:
    raw = os.getenv("APPROVED_ACTOR_IDS", "liveops01")
    return {value.strip() for value in raw.split(",") if value.strip()}
