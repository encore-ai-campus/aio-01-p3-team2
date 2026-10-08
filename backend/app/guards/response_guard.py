"""Response safety checks."""

SENSITIVE_MARKERS = ("OPENAI_API_KEY", "DATABASE_PASSWORD", "MCP_SECRET", "SSH PRIVATE KEY")


def validate_response(payload: str) -> None:
    if any(marker in payload.upper() for marker in SENSITIVE_MARKERS):
        raise ValueError("응답에 민감정보가 포함되어 있습니다.")
