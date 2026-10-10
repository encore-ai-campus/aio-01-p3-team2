"""Encrypted, access-audited 30-day storage for live LLM prompts and responses."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken

from backend.app.services.llm_service import LLMExecutionError

_IDENTITY: ContextVar[tuple[str | None, str | None] | None] = ContextVar(
    "llm_record_identity", default=None
)
_ENGINES: dict[str, object] = {}
RETENTION_DAYS = 30


class LLMRecordNotFound(LookupError):
    """The encrypted record does not exist or has expired."""


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise LLMExecutionError("Live LLM raw-record storage requires DATABASE_URL")
    return url


def _approved_actor_ids() -> set[str]:
    raw = os.getenv("APPROVED_ACTOR_IDS", "liveops01")
    return {value.strip() for value in raw.split(",") if value.strip()}


def configured_operator_actor_id() -> str:
    """Return the server-configured local operator identity, never a request value."""
    actor_id = os.getenv("OPERATOR_ACTOR_ID", "").strip()
    if not actor_id:
        raise LLMExecutionError("OPERATOR_ACTOR_ID is required for restricted LLM record access")
    return actor_id


def _fernet() -> Fernet:
    key = os.getenv("LLM_RECORD_ENCRYPTION_KEY", "").strip()
    if not key:
        raise LLMExecutionError("LLM_RECORD_ENCRYPTION_KEY is required for live mode")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise LLMExecutionError("LLM record encryption key is invalid") from exc


def _engine():
    url = _database_url()
    engine = _ENGINES.get(url)
    if engine is None:
        from sqlalchemy import create_engine

        engine = create_engine(url, pool_pre_ping=True)
        _ENGINES[url] = engine
    return engine


def validate_live_recording() -> None:
    """Fail before a provider call unless key, database, and migration are ready."""
    _fernet()
    try:
        from sqlalchemy import text

        with _engine().connect() as connection:
            connection.execute(text("SELECT 1 FROM llm_prompt_records LIMIT 0"))
    except Exception as exc:
        raise LLMExecutionError("LLM raw-record storage is unavailable") from exc


@contextmanager
def workflow_llm_identity(workflow_id: str | None, task_id: str | None):
    """Attach correlation identifiers to prompt records for one invocation."""
    token = _IDENTITY.set((workflow_id, task_id))
    try:
        yield
    finally:
        _IDENTITY.reset(token)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _configured_cost(input_tokens: int, output_tokens: int) -> str | None:
    try:
        input_price = Decimal(os.environ["LLM_INPUT_USD_PER_MILLION"])
        output_price = Decimal(os.environ["LLM_OUTPUT_USD_PER_MILLION"])
    except (KeyError, InvalidOperation):
        return None
    value = (
        Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price
    ) / 1_000_000
    return f"{value:.9f}"


def store_llm_record(
    *,
    agent_name: str,
    model_id: str,
    prompt_template_version: str,
    prompt: str,
    response: str,
    usage: dict[str, object],
) -> dict[str, object]:
    """Encrypt raw content at rest and return only safe reference metadata."""
    identity = _IDENTITY.get()
    if identity is None:
        raise LLMExecutionError("LLM raw-record workflow identity is missing")
    try:
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMExecutionError("Provider token usage is unavailable for LLM audit") from exc
    if input_tokens < 0 or output_tokens < 0:
        raise LLMExecutionError("Provider token usage is invalid")

    record_id = str(uuid4())
    now = datetime.now(UTC)
    fernet = _fernet()
    encrypted_prompt = fernet.encrypt(prompt.encode("utf-8")).decode("ascii")
    encrypted_response = fernet.encrypt(response.encode("utf-8")).decode("ascii")
    prompt_hash = _hash(prompt)
    response_hash = _hash(response)
    configured_cost = _configured_cost(input_tokens, output_tokens)
    reported_cost = usage.get("reported_cost")
    if reported_cost is not None:
        try:
            reported_cost = Decimal(str(reported_cost))
            if not reported_cost.is_finite() or reported_cost < 0:
                reported_cost = None
        except InvalidOperation:
            reported_cost = None
    from sqlalchemy import text

    with _engine().begin() as connection:
        connection.execute(
            text("DELETE FROM llm_prompt_records WHERE expires_at <= :now"), {"now": now}
        )
        connection.execute(
            text("""INSERT INTO llm_prompt_records (
                record_id, workflow_id, task_id, agent_name, provider, model_id,
                prompt_template_id, prompt_template_version, prompt_hash, response_hash,
                encrypted_prompt, encrypted_response, input_tokens, output_tokens,
                provider_reported_cost, configured_cost, cost_currency, pricing_version,
                created_at, expires_at
            ) VALUES (
                :record_id, :workflow_id, :task_id, :agent_name, 'openai', :model_id,
                :template_id, :template_version, :prompt_hash, :response_hash,
                :encrypted_prompt, :encrypted_response, :input_tokens, :output_tokens,
                :provider_reported_cost, :configured_cost, 'USD', :pricing_version,
                :created_at, :expires_at
            )"""),
            {
                "record_id": record_id,
                "workflow_id": identity[0],
                "task_id": identity[1],
                "agent_name": agent_name,
                "model_id": model_id,
                "template_id": agent_name,
                "template_version": prompt_template_version,
                "prompt_hash": prompt_hash,
                "response_hash": response_hash,
                "encrypted_prompt": encrypted_prompt,
                "encrypted_response": encrypted_response,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "provider_reported_cost": reported_cost,
                "configured_cost": configured_cost,
                "pricing_version": os.getenv("LLM_PRICING_VERSION"),
                "created_at": now,
                "expires_at": now + timedelta(days=RETENTION_DAYS),
            },
        )
    return {
        "record_id": record_id,
        "prompt_hash": prompt_hash,
        "response_hash": response_hash,
        "provider": "openai",
        "model_id": model_id,
        "prompt_template_id": agent_name,
        "prompt_template_version": prompt_template_version,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "provider_reported_cost": str(reported_cost) if reported_cost is not None else None,
        "configured_cost": configured_cost,
        "cost_currency": "USD",
        "pricing_version": os.getenv("LLM_PRICING_VERSION"),
    }


def read_llm_record(record_id: str, actor_id: str) -> dict[str, object]:
    """Audit an approved operator's raw-record read before returning plaintext."""
    if actor_id not in _approved_actor_ids():
        raise PermissionError("LLM raw-record access is not authorized")
    try:
        record_id = str(UUID(record_id))
    except (TypeError, ValueError) as exc:
        raise LLMRecordNotFound("LLM record not found") from exc
    now = datetime.now(UTC)
    audit_outcome: str | None = None
    row = None
    from sqlalchemy import text

    with _engine().begin() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM llm_prompt_records WHERE record_id=:record_id"),
                {"record_id": record_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            connection.execute(
                text("""INSERT INTO llm_prompt_record_access_audit
                    (record_id, actor_id, accessed_at, outcome)
                    VALUES (:record_id, :actor_id, :accessed_at, 'NOT_FOUND')"""),
                {"record_id": record_id, "actor_id": actor_id, "accessed_at": now},
            )
            audit_outcome = "NOT_FOUND"
        elif row["expires_at"] <= now:
            connection.execute(
                text("DELETE FROM llm_prompt_records WHERE record_id=:record_id"),
                {"record_id": record_id},
            )
            connection.execute(
                text("""INSERT INTO llm_prompt_record_access_audit
                    (record_id, actor_id, accessed_at, outcome)
                    VALUES (:record_id, :actor_id, :accessed_at, 'EXPIRED')"""),
                {"record_id": record_id, "actor_id": actor_id, "accessed_at": now},
            )
            audit_outcome = "EXPIRED"
        else:
            connection.execute(
                text("""INSERT INTO llm_prompt_record_access_audit
                    (record_id, actor_id, accessed_at, outcome)
                    VALUES (:record_id, :actor_id, :accessed_at, 'SUCCESS')"""),
                {"record_id": record_id, "actor_id": actor_id, "accessed_at": now},
            )
    if audit_outcome is not None:
        raise LLMRecordNotFound("LLM record not found")
    assert row is not None
    try:
        fernet = _fernet()
        prompt = fernet.decrypt(row["encrypted_prompt"].encode("ascii")).decode("utf-8")
        response = fernet.decrypt(row["encrypted_response"].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise LLMExecutionError("LLM raw-record decryption failed") from exc
    return {
        "record_id": record_id,
        "workflow_id": row["workflow_id"],
        "task_id": row["task_id"],
        "agent_name": row["agent_name"],
        "provider": row["provider"],
        "model_id": row["model_id"],
        "prompt_template_id": row["prompt_template_id"],
        "prompt_template_version": row["prompt_template_version"],
        "prompt_hash": row["prompt_hash"],
        "response_hash": row["response_hash"],
        "prompt": prompt,
        "response": response,
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "provider_reported_cost": str(row["provider_reported_cost"])
        if row["provider_reported_cost"] is not None
        else None,
        "configured_cost": str(row["configured_cost"])
        if row["configured_cost"] is not None
        else None,
        "cost_currency": row["cost_currency"],
        "pricing_version": row["pricing_version"],
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
    }


def purge_expired_llm_records() -> int:
    """Delete expired raw records while retaining their access audit history."""
    from sqlalchemy import text

    with _engine().begin() as connection:
        result = connection.execute(
            text("DELETE FROM llm_prompt_records WHERE expires_at <= :now"),
            {"now": datetime.now(UTC)},
        )
    return int(result.rowcount or 0)
