"""Small deterministic input guard for the MVP."""

BLOCKED_PHRASES = ("ignore previous instructions", "system prompt", "api key")


def validate_text(value: str) -> None:
    lowered = value.casefold()
    if any(phrase in lowered for phrase in BLOCKED_PHRASES):
        raise ValueError("허용되지 않은 문구가 포함되어 있습니다.")
