"""Impact rules cover changed roles, missing history and versioned manifests."""

import subprocess
from datetime import UTC, datetime

import pytest
from infra.aws.contracts import CleanupPlan, EnvironmentManifest, ReleaseManifest, export
from infra.aws.deployment_impact import ROLES, classify, load_rules, plan_from_git
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("path", "targets"),
    [
        ("frontend-web/src/app.js", ["plan-web"]),
        ("demo-game/src/game.js", ["game-web"]),
        ("backend/app/services/workflow_service.py", ["main-api", "fault-api"]),
        ("mcp_server/server.py", ["main-mcp", "fault-mcp"]),
        ("mcp_server/tools/_db.py", list(ROLES[2:])),
        ("backend/requirements.txt", list(ROLES[2:])),
        ("backend/app/game/rules.py", list(ROLES)),
        ("backend/app/schemas/planning.py", list(ROLES)),
        ("backend/sql/017_example.sql", list(ROLES)),
        ("backend/tests/support/fault_lab_runner.py", ["main-api", "fault-api"]),
        ("backend/tests/unit/test_x.py", []),
        ("frontend-web/e2e/test.spec.js", []),
        ("docs/ik/README.md", []),
        ("frontend/app.py", []),
    ],
)
def test_classified_roles(path, targets):
    result = classify([path])
    assert result["targets"] == targets
    assert not result["manual_review_required"]


def test_non_runtime_and_unknown_paths():
    assert not classify(["docs/ik/plan.md"])["ci_required"]
    assert classify(["backend/tests/unit/test_x.py"])["ci_required"]
    assert classify(["infra/aws/stacks/network.yml"])["manual_review_required"]
    assert classify(["infra/test-suites.json"])["targets"] == []
    unknown = classify(["new-runtime.py"])
    assert unknown["targets"] == list(ROLES) and unknown["manual_review_required"]
    assert classify(["backend/sql/017_example.sql"])["migration_required"]
    result = classify(["demo-game/src/game.js", "frontend-web/src/app.js"])
    assert result["images"] == ["web"] and result["web_sites"] == ["plan", "game"]


@pytest.mark.parametrize("path", ["/etc/key", "../key", "C:/key", "backend/.env", ".env.prod"])
def test_private_or_non_repository_paths_are_rejected(path):
    with pytest.raises(ValueError):
        classify([path])


def test_invalid_rules_are_rejected(tmp_path):
    rules = load_rules()
    rules["rules"][0]["targets"] = ["unknown"]
    import json

    target = tmp_path / "rules.json"
    target.write_text(json.dumps(rules), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown deployment"):
        load_rules(target)


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root, path):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fixture\n", encoding="utf-8")
    _git(root, "add", "--", path)
    _git(root, "commit", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD")


def test_last_successful_baseline_and_renames(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.com")
    base = _commit(tmp_path, "README.md")
    failed = _commit(tmp_path, "backend/app/service.py")
    head = _commit(tmp_path, "demo-game/src/game.js")
    assert plan_from_git(failed, head, root=tmp_path)["targets"] == ["game-web"]
    deployed = plan_from_git(base, head, baseline="deployed", root=tmp_path)
    assert deployed["targets"] == ["game-web", "main-api", "fault-api"]
    assert not deployed["deployment_authorized"]
    (tmp_path / "frontend-web").mkdir()
    _git(tmp_path, "mv", "backend/app/service.py", "frontend-web/service.py")
    _git(tmp_path, "commit", "-m", "move fixture")
    moved = _git(tmp_path, "rev-parse", "HEAD")
    assert plan_from_git(head, moved, root=tmp_path)["targets"] == [
        "plan-web",
        "main-api",
        "fault-api",
    ]
    with pytest.raises(ValueError, match="ancestor"):
        plan_from_git(moved, base, root=tmp_path)
    assert plan_from_git("0" * 40, moved, root=tmp_path)["full_deployment"]
    with pytest.raises(ValueError, match="hexadecimal"):
        plan_from_git(base, "HEAD", root=tmp_path)


def _resources():
    return [
        {
            "id": f"i-{index:017x}",
            "kind": "ec2",
            "owner_stack": "fixture-app",
            "environment_id": "ikonly-demo",
            "retain": False,
        }
        for index in range(6)
    ]


def test_environment_and_cleanup_contract_scope():
    resources = _resources()
    environment = {
        "account_id": "123456789012",
        "region": "ap-northeast-2",
        "environment_id": "ikonly-demo",
        "roles": {role: item["id"] for role, item in zip(ROLES, resources, strict=True)},
        "resources": resources,
        "parameter_names": {"openai": "/multi-agent-autohealing/ikonly-demo/openai"},
        "stop_after": datetime.now(UTC),
    }
    EnvironmentManifest.model_validate(environment)
    with pytest.raises(ValidationError):
        EnvironmentManifest.model_validate({**environment, "stop_after": "2026-10-12T12:00:00"})
    cleanup = {
        "account_id": "123456789012",
        "region": "ap-northeast-2",
        "environment_id": "ikonly-demo",
        "mode": "cleanup-keep-data",
        "delete": resources,
        "retain": [],
        "environment_manifest_checksum": "sha256:" + "a" * 64,
    }
    CleanupPlan.model_validate(cleanup)
    with pytest.raises(ValidationError):
        CleanupPlan.model_validate({**cleanup, "retain": [resources[0]]})
    protected = {**resources[0], "retain": True}
    with pytest.raises(ValidationError):
        CleanupPlan.model_validate({**cleanup, "delete": [protected]})
    wrong_environment = {**resources[0], "environment_id": "other"}
    with pytest.raises(ValidationError):
        CleanupPlan.model_validate({**cleanup, "delete": [wrong_environment]})


def test_release_contains_retained_roles_and_schema_export():
    services = {
        role: {
            "image": "web" if role.endswith("-web") else "application",
            "digest": "sha256:" + "a" * 64,
            "source_sha": "b" * 40,
        }
        for role in ROLES
    }
    payload = {
        "environment_id": "ikonly-demo",
        "source_sha": "c" * 40,
        "ci_run_id": "100",
        "ci_attempt": 1,
        "services": services,
        "previous_release_id": None,
        "schema_checksum": "sha256:" + "d" * 64,
        "migration_checksum": "sha256:" + "e" * 64,
        "catalog_checksum": "sha256:" + "f" * 64,
    }
    ReleaseManifest.model_validate(payload)
    services["fault-api"] = {**services["fault-api"], "digest": "sha256:" + "0" * 64}
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(payload)
    assert not export(check=True)
