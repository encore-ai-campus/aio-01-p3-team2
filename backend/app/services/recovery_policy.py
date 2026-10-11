"""Persistent, fail-closed recovery policy lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config.deployment import deployment_mode
from backend.app.faults.error_catalog import ERROR_CATALOG
from backend.app.services.settings import configured_operator_actor_id

POLICY_ID = "planning.default"
HARD_MAX_ATTEMPTS = 4
ALLOWED_ACTIONS = {"FAIL_FAST", "RETRY_OPERATION", "RETRY_AGENT", "REVISE_PLAN", "SAFE_STOP"}
FORBIDDEN_RETRY_PREFIXES = ("ERR-SEC-", "ERR-CONTRACT-HANDOFF-", "ERR-STORE-DB-002")
_PINNED_POLICY: ContextVar[dict[str, Any] | None] = ContextVar(
    "pinned_recovery_policy", default=None
)
_ENGINES: dict[str, Any] = {}


class RecoveryPolicyError(RuntimeError):
    """Execution is blocked because the active policy contract is unavailable."""


def _engine() -> Any:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required for recovery policy management")
    engine = _ENGINES.get(url)
    if engine is None:
        engine = create_engine(url, pool_pre_ping=True)
        _ENGINES[url] = engine
    return engine


def _default_policy() -> dict[str, Any]:
    routes = {}
    for definition in ERROR_CATALOG.values():
        action = {
            "NEVER": "SAFE_STOP",
            "OPERATION": "RETRY_OPERATION",
            "AGENT": "RETRY_AGENT",
            "PLAN": "REVISE_PLAN",
        }.get(definition.retry_class, "SAFE_STOP")
        if definition.code.startswith("ERR-SEC-") or definition.code.startswith(
            "ERR-CONTRACT-HANDOFF-"
        ):
            action = "SAFE_STOP"
        routes[definition.code] = {
            "action": action,
            "max_attempts": 4
            if action.startswith("RETRY_")
            else 3
            if action == "REVISE_PLAN"
            else 1,
        }
    return {"policy_id": POLICY_ID, "routes": routes}


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(policy) != {"policy_id", "routes"} or policy.get("policy_id") != POLICY_ID:
        errors.append("policy_id와 routes만 지정할 수 있습니다.")
        return errors
    routes = policy.get("routes")
    if not isinstance(routes, dict) or not routes:
        return ["routes는 비어 있지 않은 객체여야 합니다."]
    known = {item.code for item in ERROR_CATALOG.values()}
    for missing in sorted(known - set(routes)):
        errors.append(f"대응 경로가 누락됐습니다: {missing}")
    for code, route in routes.items():
        if code not in known:
            errors.append(f"등록되지 않은 오류 코드: {code}")
            continue
        if not isinstance(route, dict) or set(route) != {"action", "max_attempts"}:
            errors.append(f"{code}: action과 max_attempts만 지정할 수 있습니다.")
            continue
        action = route["action"]
        attempts = route["max_attempts"]
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            errors.append(f"{code}: 허용되지 않은 action입니다.")
        max_allowed = 3 if action == "REVISE_PLAN" else HARD_MAX_ATTEMPTS
        if type(attempts) is not int or not 1 <= attempts <= max_allowed:
            errors.append(f"{code}: max_attempts는 1~{max_allowed} 정수여야 합니다.")
        if code.startswith(FORBIDDEN_RETRY_PREFIXES) and action in {
            "RETRY_OPERATION",
            "RETRY_AGENT",
            "REVISE_PLAN",
        }:
            errors.append(
                f"{code}: 보안·계약·결과불명 쓰기 오류는 재시도하거나 수정할 수 없습니다."
            )
        if action not in {"RETRY_OPERATION", "RETRY_AGENT", "REVISE_PLAN"} and attempts != 1:
            errors.append(f"{code}: 재시도 action이 아니면 max_attempts는 1이어야 합니다.")
        definition = next((item for item in ERROR_CATALOG.values() if item.code == code), None)
        permitted = {
            "NEVER": {"SAFE_STOP", "FAIL_FAST"},
            "OPERATION": {"RETRY_OPERATION", "SAFE_STOP", "FAIL_FAST"},
            "AGENT": {"RETRY_AGENT", "SAFE_STOP", "FAIL_FAST"},
            "PLAN": {"REVISE_PLAN", "SAFE_STOP", "FAIL_FAST"},
        }.get(definition.retry_class if definition else "NEVER", {"SAFE_STOP"})
        if action not in permitted:
            errors.append(f"{code}: 카탈로그 기본 분류에서 허용되지 않는 대응입니다.")
    return errors


@contextmanager
def pin_active_policy():
    """Pin one immutable active policy for a Workflow run."""
    list_policies()
    with _engine().begin() as connection:
        row = connection.execute(
            text(
                "SELECT version, policy FROM recovery_policy_versions WHERE policy_id=:id AND status='ACTIVE'"
            ),
            {"id": POLICY_ID},
        ).first()
        if row is None:
            raise RecoveryPolicyError("활성 복구 정책이 없습니다. 정책을 검증·활성화하세요.")
        policy = json.loads(row.policy) if isinstance(row.policy, str) else row.policy
        # A reviewed compatibility mapping preserves immutable historical policies.
        # Only the split MCP rule inherits its former shared rule; all other missing codes fail closed.
        effective, compatibility = effective_stored_policy(policy)
        errors = validate_policy(effective)
        if errors:
            raise RecoveryPolicyError(
                "활성 정책이 현재 오류 계약을 충족하지 않습니다. 새 초안을 검증·활성화하세요."
            )
        pinned = {
            "version": row.version,
            **effective,
            **policy_fingerprint(policy),
            "compatibility_mappings": compatibility,
            "effective_policy_hash": policy_fingerprint(effective)["policy_hash"],
        }
    token = _PINNED_POLICY.set(pinned)
    try:
        yield pinned
    finally:
        _PINNED_POLICY.reset(token)


def effective_stored_policy(policy: dict[str, Any]) -> tuple[dict, dict]:
    """Read-only compatibility for the one explicitly split historical rule."""
    effective = deepcopy(policy)
    mappings = {}
    routes = effective.get("routes", {})
    if isinstance(routes, dict) and "ERR-DEP-MCP-003" not in routes and "ERR-DEP-MCP-002" in routes:
        routes["ERR-DEP-MCP-003"] = deepcopy(routes["ERR-DEP-MCP-002"])
        mappings = {"ERR-DEP-MCP-003": "ERR-DEP-MCP-002"}
    return effective, mappings


def pinned_route(error_code: str) -> dict[str, Any] | None:
    policy = _PINNED_POLICY.get()
    return policy.get("routes", {}).get(error_code) if policy else None


def has_pinned_policy() -> bool:
    return _PINNED_POLICY.get() is not None


def pinned_policy_version() -> int | None:
    policy = _PINNED_POLICY.get()
    return policy.get("version") if policy else None


def policy_fingerprint(policy: dict[str, Any]) -> dict[str, str]:
    def digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    return {
        "policy_hash": digest(policy),
        "error_catalog_version": digest({name: asdict(row) for name, row in ERROR_CATALOG.items()}),
        "hard_limit_version": digest(
            {
                "operation_attempts": HARD_MAX_ATTEMPTS,
                "plan_attempts": 3,
                "workflow_seconds": 180,
                "llm_calls": 8,
                "llm_limits": {
                    name: os.getenv(name, "").strip()
                    for name in (
                        "LLM_MAX_INPUT_TOKENS",
                        "LLM_MAX_OUTPUT_TOKENS",
                        "LLM_MAX_COST_USD",
                        "LLM_INPUT_USD_PER_MILLION",
                        "LLM_OUTPUT_USD_PER_MILLION",
                        "LLM_PRICING_VERSION",
                        "LLM_COST_CURRENCY",
                    )
                },
            }
        ),
    }


def pinned_policy_reference() -> dict[str, Any] | None:
    policy = _PINNED_POLICY.get()
    if policy is None:
        return None
    return {
        key: policy[key]
        for key in (
            "policy_id",
            "version",
            "policy_hash",
            "error_catalog_version",
            "hard_limit_version",
            "compatibility_mappings",
            "effective_policy_hash",
        )
        if key in policy
    }


def _audit(connection: Any, version: int, action: str, details: dict[str, Any]) -> None:
    connection.execute(
        text("""INSERT INTO recovery_policy_audit
        (policy_id, version, action, actor, details, created_at)
        VALUES (:policy_id, :version, :action, :actor, :details, :created_at)"""),
        {
            "policy_id": POLICY_ID,
            "version": version,
            "action": action,
            "actor": configured_operator_actor_id(),
            "details": json.dumps(details),
            "created_at": datetime.now(UTC),
        },
    )


def _row(row: Any) -> dict[str, Any]:
    result = dict(row._mapping)
    if isinstance(result["policy"], str):
        result["policy"] = json.loads(result["policy"])
    if isinstance(result["validation"], str):
        result["validation"] = json.loads(result["validation"])
    return result


def list_policies() -> dict[str, Any]:
    with _engine().begin() as connection:
        rows = connection.execute(
            text(
                "SELECT * FROM recovery_policy_versions WHERE policy_id=:id ORDER BY version DESC"
            ),
            {"id": POLICY_ID},
        ).fetchall()
        if not rows:
            if deployment_mode() == "cloud":
                raise RecoveryPolicyError(
                    "활성 정책 bootstrap이 필요합니다. 운영자가 승인한 정책을 준비하세요."
                )
            connection.execute(
                text("""INSERT INTO recovery_policy_versions
                (policy_id, version, status, idempotency_key, request_hash, policy, validation, created_at)
                VALUES (:id, 1, 'ACTIVE', 'bootstrap-policy-v1', :hash, CAST(:policy AS JSONB), CAST(:validation AS JSONB), :created)"""),
                {
                    "id": POLICY_ID,
                    "hash": hashlib.sha256(b"bootstrap-policy-v1").hexdigest(),
                    "policy": json.dumps(_default_policy()),
                    "validation": json.dumps({"valid": True, "errors": []}),
                    "created": datetime.now(UTC),
                },
            )
            _audit(connection, 1, "ACTIVATE", {"bootstrap": True})
            rows = connection.execute(
                text(
                    "SELECT * FROM recovery_policy_versions WHERE policy_id=:id ORDER BY version DESC"
                ),
                {"id": POLICY_ID},
            ).fetchall()
        audit = connection.execute(
            text(
                "SELECT audit_id, version, action, actor, details, created_at FROM recovery_policy_audit WHERE policy_id=:id ORDER BY audit_id DESC LIMIT 50"
            ),
            {"id": POLICY_ID},
        ).fetchall()
    versions = [_row(row) for row in rows]
    for version in versions:
        version["effective_reference"] = {
            "version": version["version"],
            **policy_fingerprint(version["policy"]),
        }
        effective, compatibility = (
            effective_stored_policy(version["policy"])
            if version["status"] != "DRAFT"
            else (version["policy"], {})
        )
        version["compatibility_mappings"] = compatibility
        version["current_contract_errors"] = validate_policy(effective)
    return {
        "versions": versions,
        "suggested_draft": _default_policy(),
        "audit": [dict(row._mapping) for row in audit],
        "hard_max_attempts": HARD_MAX_ATTEMPTS,
        "error_catalog": {
            definition.code: {
                "category": definition.category,
                "location": definition.location,
                "retry_class": definition.retry_class,
                "safe_message": definition.safe_message,
            }
            for definition in ERROR_CATALOG.values()
        },
    }


def create_draft(policy: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    errors = validate_policy(policy)
    request_hash = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with _engine().begin() as connection:
        prior = connection.execute(
            text(
                "SELECT version, request_hash, status FROM recovery_policy_versions WHERE idempotency_key=:key"
            ),
            {"key": idempotency_key},
        ).first()
        if prior:
            if prior.request_hash != request_hash:
                raise ValueError("Idempotency-Key was already used for a different policy")
            return {
                "policy_id": POLICY_ID,
                "version": prior.version,
                "status": prior.status,
                "validation": {"valid": not errors, "errors": errors},
            }
        latest = connection.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) FROM recovery_policy_versions WHERE policy_id=:id"
            ),
            {"id": POLICY_ID},
        ).scalar_one()
        version = int(latest) + 1
        connection.execute(
            text("""INSERT INTO recovery_policy_versions
            (policy_id, version, status, idempotency_key, request_hash, policy, validation, created_at)
            VALUES (:id, :version, 'DRAFT', :key, :hash, CAST(:policy AS JSONB), CAST(:validation AS JSONB), :created)"""),
            {
                "id": POLICY_ID,
                "version": version,
                "policy": json.dumps(policy),
                "validation": json.dumps({"valid": not errors, "errors": errors}),
                "created": datetime.now(UTC),
                "key": idempotency_key,
                "hash": request_hash,
            },
        )
        _audit(
            connection,
            version,
            "CREATE",
            {
                "errors": errors,
                "digest": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
            },
        )
    return {
        "policy_id": POLICY_ID,
        "version": version,
        "status": "DRAFT",
        "validation": {"valid": not errors, "errors": errors},
    }


def validate_version(version: int) -> dict[str, Any]:
    with _engine().begin() as connection:
        row = connection.execute(
            text(
                "SELECT policy FROM recovery_policy_versions WHERE policy_id=:id AND version=:version AND status='DRAFT' FOR UPDATE"
            ),
            {"id": POLICY_ID, "version": version},
        ).first()
        if not row:
            raise LookupError("초안 버전을 찾을 수 없습니다.")
        policy = json.loads(row.policy) if isinstance(row.policy, str) else row.policy
        errors = validate_policy(policy)
        validation = {"valid": not errors, "errors": errors}
        connection.execute(
            text(
                "UPDATE recovery_policy_versions SET validation=CAST(:validation AS JSONB) WHERE policy_id=:id AND version=:version"
            ),
            {"validation": json.dumps(validation), "id": POLICY_ID, "version": version},
        )
        _audit(connection, version, "VALIDATE", validation)
    return validation


def preview_version(version: int) -> dict[str, Any]:
    with _engine().begin() as connection:
        rows = connection.execute(
            text(
                "SELECT policy, status FROM recovery_policy_versions WHERE policy_id=:id AND version=:version"
            ),
            {"id": POLICY_ID, "version": version},
        ).first()
        if not rows:
            raise LookupError("정책 버전을 찾을 수 없습니다.")
        candidate = json.loads(rows.policy) if isinstance(rows.policy, str) else rows.policy
        active_row = connection.execute(
            text(
                "SELECT policy FROM recovery_policy_versions WHERE policy_id=:id AND status='ACTIVE'"
            ),
            {"id": POLICY_ID},
        ).first()
        active = (
            (
                json.loads(active_row.policy)
                if isinstance(active_row.policy, str)
                else active_row.policy
            )
            if active_row
            else _default_policy()
        )
        validation_candidate, compatibility = (
            effective_stored_policy(candidate) if rows.status != "DRAFT" else (candidate, {})
        )
        validation = validate_policy(validation_candidate)
        changes = [
            {"error_code": code, "before": active["routes"].get(code), "after": route}
            for code, route in candidate.get("routes", {}).items()
            if active["routes"].get(code) != route
        ]
        result = {
            "valid": not validation,
            "errors": validation,
            "runtime_mutated": False,
            "changed_routes": changes,
            "compatibility_mappings": compatibility,
        }
        _audit(connection, version, "PREVIEW", result)
    return result


def _policy_operation_result(
    actor: str, operation: str, idempotency_key: str, request_hash: str
) -> dict[str, Any] | None:
    with _engine().connect() as connection:
        row = connection.execute(
            text("""SELECT request_hash, response FROM recovery_policy_operations
            WHERE actor=:actor AND operation=:operation AND idempotency_key=:key"""),
            {"actor": actor, "operation": operation, "key": idempotency_key},
        ).first()
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise ValueError("Idempotency-Key was already used for a different policy operation")
    response = row.response
    return json.loads(response) if isinstance(response, str) else response


def activate_version(
    version: int, *, idempotency_key: str, rollback: bool = False
) -> dict[str, Any]:
    operation = "ROLLBACK" if rollback else "ACTIVATE"
    actor = configured_operator_actor_id()
    request_hash = hashlib.sha256(
        json.dumps(
            {"policy_id": POLICY_ID, "version": version, "operation": operation},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    prior = _policy_operation_result(actor, operation, idempotency_key, request_hash)
    if prior is not None:
        return prior
    preview = preview_version(version)
    if not preview["valid"]:
        raise ValueError("검증되지 않았거나 안전 조건을 위반한 정책입니다.")
    response = {
        "policy_id": POLICY_ID,
        "version": version,
        "status": "ACTIVE",
        "action": operation,
    }
    try:
        with _engine().begin() as connection:
            lock_key = f"{actor}:{operation}:{idempotency_key}"
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            row = connection.execute(
                text("""SELECT request_hash, response FROM recovery_policy_operations
                WHERE actor=:actor AND operation=:operation AND idempotency_key=:key"""),
                {"actor": actor, "operation": operation, "key": idempotency_key},
            ).first()
            if row:
                if row.request_hash != request_hash:
                    raise ValueError(
                        "Idempotency-Key was already used for a different policy operation"
                    )
                return json.loads(row.response) if isinstance(row.response, str) else row.response

            row = connection.execute(
                text("""SELECT status FROM recovery_policy_versions
                WHERE policy_id=:id AND version=:version FOR UPDATE"""),
                {"id": POLICY_ID, "version": version},
            ).first()
            if not row:
                raise LookupError("정책 버전을 찾을 수 없습니다.")
            expected_status = "RETIRED" if rollback else "DRAFT"
            if row.status != expected_status:
                raise ValueError(f"{expected_status} 상태의 정책 버전만 이 작업을 할 수 있습니다.")
            previous = connection.execute(
                text(
                    "SELECT version FROM recovery_policy_versions WHERE policy_id=:id AND status='ACTIVE' FOR UPDATE"
                ),
                {"id": POLICY_ID},
            ).first()
            connection.execute(
                text(
                    "UPDATE recovery_policy_versions SET status='RETIRED' WHERE policy_id=:id AND status='ACTIVE'"
                ),
                {"id": POLICY_ID},
            )
            connection.execute(
                text(
                    "UPDATE recovery_policy_versions SET status='ACTIVE' WHERE policy_id=:id AND version=:version"
                ),
                {"id": POLICY_ID, "version": version},
            )
            _audit(
                connection,
                version,
                operation,
                {"previous_version": previous.version if previous else None},
            )
            connection.execute(
                text("""INSERT INTO recovery_policy_operations
                (actor, operation, idempotency_key, policy_id, version, request_hash, response, created_at)
                VALUES (:actor, :operation, :key, :policy_id, :version, :hash, CAST(:response AS JSONB), :created)"""),
                {
                    "actor": actor,
                    "operation": operation,
                    "key": idempotency_key,
                    "policy_id": POLICY_ID,
                    "version": version,
                    "hash": request_hash,
                    "response": json.dumps(response),
                    "created": datetime.now(UTC),
                },
            )
    except SQLAlchemyError as exc:
        try:
            committed = _policy_operation_result(actor, operation, idempotency_key, request_hash)
        except (SQLAlchemyError, RuntimeError):
            committed = None
        if committed is not None:
            return committed
        raise RuntimeError("복구 정책 변경 결과를 확인할 수 없습니다.") from exc
    return response
