"""Backend A environment settings without secret values."""

import os


class OperatorIdentityError(RuntimeError):
    """Raised when the single server-owned operator identity is unsafe to use."""


def approved_actor_ids() -> set[str]:
    raw = os.getenv("APPROVED_ACTOR_IDS", "liveops01")
    return {value.strip() for value in raw.split(",") if value.strip()}


def configured_operator_actor_id() -> str:
    """Resolve the single server-owned operator identity and enforce its allowlist."""
    approved = approved_actor_ids()
    configured = os.getenv("OPERATOR_ACTOR_ID", "").strip()
    if not configured:
        if len(approved) != 1:
            raise OperatorIdentityError(
                "OPERATOR_ACTOR_ID is required when multiple actors are approved"
            )
        configured = next(iter(approved))
    if configured not in approved:
        raise OperatorIdentityError("OPERATOR_ACTOR_ID must be included in APPROVED_ACTOR_IDS")
    return configured
