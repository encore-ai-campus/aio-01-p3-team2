"""Small deterministic input guard for the MVP."""

import re
import unicodedata

BLOCKED_PHRASES = ("ignore previous instructions", "system prompt", "api key")
BLOCKED_PATTERNS = (
    re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:(?:all|any|the)\s+)*(?:previous|prior|above|earlier|system|developer)\s+(?:instructions?|rules?|prompts?)\b"
    ),
    re.compile(
        r"\b(?:reveal|disclose|print|show|repeat|dump|expose)\s+(?:(?:the|your|full|hidden|confidential|private|system|developer)\s+){0,4}(?:prompt|instructions?|api\s+key|secret|credentials?)\b"
    ),
    re.compile(
        r"\b(?:act\s+as|pretend\s+to\s+be|you\s+are\s+now)\s+(?:(?:an?|the)\s+)?(?:unrestricted|system|developer|admin|root|jailbroken)\b"
    ),
    re.compile(
        r"\b(?:bypass|disable|ignore)\s+(?:the\s+)?(?:safety|security)\s+(?:guard|rules?|policy|checks?)\b"
    ),
    re.compile(r"(?:이전|앞선|위의)\s*(?:지침|지시|규칙)\s*(?:을|를)?\s*(?:무시|잊|따르지)"),
    re.compile(r"(?:시스템|개발자)\s*(?:프롬프트|지침|지시)\s*(?:출력|공개|노출|유출|보여)"),
)


def _normalized_text(value: str) -> str:
    compatibility_text = unicodedata.normalize("NFKC", value)
    without_format_chars = "".join(
        char for char in compatibility_text if unicodedata.category(char) != "Cf"
    )
    folded = without_format_chars.casefold().replace("_", " ")
    return re.sub(r"[^\w]+", " ", folded).strip()


class InputGuardBlocked(ValueError):
    """Input was rejected by the deterministic security guard."""


def validate_text(value: str) -> None:
    normalized = _normalized_text(value)
    if any(phrase in normalized for phrase in BLOCKED_PHRASES) or any(
        pattern.search(normalized) for pattern in BLOCKED_PATTERNS
    ):
        raise InputGuardBlocked("허용되지 않은 문구가 포함되어 있습니다.")
