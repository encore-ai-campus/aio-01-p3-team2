"""Persistent, fail-closed recovery policy lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text

from backend.app.faults.error_catalog import ERROR_CATALOG

POLICY_ID = "planning.default"
HARD_MAX_ATTEMPTS = 4
ALLOWED_ACTIONS = {"FAIL_FAST", "RETRY_OPERATION", "RETRY_AGENT", "REVISE_PLAN", "SAFE_STOP"}
FORBIDDEN_RETRY_PREFIXES = ("ERR-SEC-", "ERR-CONTRACT-HANDOFF-", "ERR-STORE-DB-002")
_PINNED_POLICY: ContextVar[dict[str, Any] | None] = ContextVar(
    "pinned_recovery_policy", default=None
)
_ENGINES: dict[str, Any] = {}


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
        if definition.code.startswith("ERR-SEC-") or definition.code.startswith("ERR-CONTRACT-"):
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
            raise RuntimeError("활성 복구 정책이 없습니다. 오류 복구를 fail-closed 처리합니다.")
        policy = json.loads(row.policy) if isinstance(row.policy, str) else row.policy
        pinned = {"version": row.version, **policy}
    token = _PINNED_POLICY.set(pinned)
    try:
        yield pinned
    finally:
        _PINNED_POLICY.reset(token)


def pinned_route(error_code: str) -> dict[str, Any] | None:
    policy = _PINNED_POLICY.get()
    return policy.get("routes", {}).get(error_code) if policy else None


def has_pinned_policy() -> bool:
    return _PINNED_POLICY.get() is not None


def pinned_policy_version() -> int | None:
    policy = _PINNED_POLICY.get()
    return policy.get("version") if policy else None


def _audit(connection: Any, version: int, action: str, details: dict[str, Any]) -> None:
    connection.execute(
        text("""INSERT INTO recovery_policy_audit
        (policy_id, version, action, actor, details, created_at)
        VALUES (:policy_id, :version, :action, 'local-operator', :details, :created_at)"""),
        {
            "policy_id": POLICY_ID,
            "version": version,
            "action": action,
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
    default_routes = _default_policy()["routes"]
    for version in versions:
        routes = version["policy"].setdefault("routes", {})
        for code, route in default_routes.items():
            routes.setdefault(code, route)
    return {
        "versions": versions,
        "audit": [dict(row._mapping) for row in audit],
        "hard_max_attempts": HARD_MAX_ATTEMPTS,
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
        if rows.status == "RETIRED":
            for code, route in _default_policy()["routes"].items():
                candidate["routes"].setdefault(code, route)
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
        validation = validate_policy(candidate)
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
        }
        _audit(connection, version, "PREVIEW", result)
    return result


def activate_version(version: int, *, rollback: bool = False) -> dict[str, Any]:
    preview = preview_version(version)
    if not preview["valid"]:
        raise ValueError("검증되지 않았거나 안전 조건을 위반한 정책입니다.")
    with _engine().begin() as connection:
        row = connection.execute(
            text(
                "SELECT status FROM recovery_policy_versions WHERE policy_id=:id AND version=:version FOR UPDATE"
            ),
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
            "ROLLBACK" if rollback else "ACTIVATE",
            {"previous_version": previous.version if previous else None},
        )
    return {
        "policy_id": POLICY_ID,
        "version": version,
        "status": "ACTIVE",
        "action": "ROLLBACK" if rollback else "ACTIVATE",
    }
