"""Encrypted LLM prompt retention and operator access audit on PostgreSQL."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.main import app
from backend.app.services.llm_record_service import (
    LLMRecordNotFound,
    read_llm_record,
    store_llm_record,
    workflow_llm_identity,
)
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def _store_record(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, str]:
    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("LLM_RECORD_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("APPROVED_ACTOR_IDS", "liveops01")
    monkeypatch.setenv("OPERATOR_ACTOR_ID", "liveops01")
    workflow_id = f"WF-{uuid4()}"
    prompt = f"private prompt {uuid4()}"
    response = f"private response {uuid4()}"
    with workflow_llm_identity(workflow_id, f"TASK-{uuid4()}"):
        metadata = store_llm_record(
            agent_name="canonical_event_planner_agent",
            model_id="test-model",
            prompt_template_version="test-template-v1",
            prompt=prompt,
            response=response,
            usage={"input_tokens": 11, "output_tokens": 7, "reported_cost": "0.001"},
        )
    return str(metadata["record_id"]), prompt, response


def test_raw_prompt_is_encrypted_and_operator_read_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id, prompt, response = _store_record(monkeypatch)
    engine = create_engine(os.environ["POSTGRES_TEST_URL"])
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT encrypted_prompt, encrypted_response, prompt_hash, response_hash "
                    "FROM llm_prompt_records WHERE record_id=:record_id"
                ),
                {"record_id": record_id},
            ).one()
            assert prompt not in row.encrypted_prompt
            assert response not in row.encrypted_response
            assert row.prompt_hash
            assert row.response_hash

        result = read_llm_record(record_id, "liveops01")
        assert result["prompt"] == prompt
        assert result["response"] == response
        assert result["input_tokens"] == 11
        assert result["output_tokens"] == 7
        with engine.connect() as connection:
            audit = connection.execute(
                text(
                    "SELECT actor_id, outcome FROM llm_prompt_record_access_audit "
                    "WHERE record_id=:record_id ORDER BY access_id DESC LIMIT 1"
                ),
                {"record_id": record_id},
            ).one()
        assert audit == ("liveops01", "SUCCESS")
    finally:
        engine.dispose()


def test_record_read_uses_server_operator_identity_not_request_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id, prompt, _ = _store_record(monkeypatch)
    response = TestClient(app).get(
        f"/api/v1/llm-records/{record_id}", headers={"X-Actor-ID": "attacker"}
    )
    assert response.status_code == 200
    assert response.json()["prompt"] == prompt


def test_expired_record_is_deleted_and_access_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_id, _, _ = _store_record(monkeypatch)
    engine = create_engine(os.environ["POSTGRES_TEST_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE llm_prompt_records SET created_at=:created, expires_at=:expired "
                    "WHERE record_id=:record_id"
                ),
                {
                    "record_id": record_id,
                    "created": datetime.now(UTC) - timedelta(days=2),
                    "expired": datetime.now(UTC) - timedelta(days=1),
                },
            )
        with pytest.raises(LLMRecordNotFound):
            read_llm_record(record_id, "liveops01")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT outcome FROM llm_prompt_record_access_audit "
                    "WHERE record_id=:record_id ORDER BY access_id DESC LIMIT 1"
                ),
                {"record_id": record_id},
            ).one()
            remaining = connection.execute(
                text("SELECT count(*) FROM llm_prompt_records WHERE record_id=:record_id"),
                {"record_id": record_id},
            ).scalar_one()
        assert row.outcome == "EXPIRED"
        assert remaining == 0
    finally:
        engine.dispose()


def test_unapproved_actor_cannot_read_raw_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    record_id, _, _ = _store_record(monkeypatch)
    with pytest.raises(PermissionError):
        read_llm_record(record_id, "unapproved-operator")
