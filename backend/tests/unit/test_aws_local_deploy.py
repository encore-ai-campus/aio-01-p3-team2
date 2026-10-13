"""Local AWS operations and reviewed host release guards."""

import copy
import hashlib
import json

import pytest
from infra.aws.host_release import selected_image
from infra.aws.iac import ROLES
from infra.aws.ssm_operations import send, status


def test_host_image_selection_binds_manifest_hash_source_role_and_account():
    sha = "a" * 40
    manifest = {
        "environment_id": "ikonly-demo",
        "source_sha": sha,
        "services": {
            role: {
                "image": "web" if role.endswith("-web") else "application",
                "digest": "sha256:" + "b" * 64,
                "source_sha": sha,
            }
            for role in ROLES
        },
    }
    raw = json.dumps(manifest).encode()
    digest = hashlib.sha256(raw).hexdigest()
    assert selected_image(manifest, "plan-web", "123456789012", sha, digest, raw).endswith(
        "/web@sha256:" + "b" * 64
    )
    with pytest.raises(ValueError):
        selected_image(manifest, "plan-web", "123456789012", sha, "c" * 64, raw)
    for key, value in (("source_sha", "c" * 40), ("environment_id", "other")):
        bad = copy.deepcopy(manifest)
        bad[key] = value
        with pytest.raises(ValueError):
            selected_image(bad, "main-api", "123456789012", sha, digest, raw)
    manifest["services"]["main-api"]["image"] = "web"
    with pytest.raises(ValueError):
        selected_image(manifest, "main-api", "123456789012", sha, digest, raw)


def test_local_ssm_operations_require_owned_running_instance_and_hide_output():
    config = {
        "account_id": "123456789012",
        "region": "ap-northeast-2",
        "environment_id": "ikonly-demo",
        "stop_after": None,
        "termination_mode": "manual",
    }
    tags = [
        {"Key": "Environment", "Value": "ikonly-demo"},
        {"Key": "ManagedBy", "Value": "ikonly-iac"},
    ]
    requests = []

    def aws(*args, **kwargs):
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/operator"}
        if args[:2] == ("ec2", "describe-instances"):
            return {"Reservations": [{"Instances": [{"Tags": tags, "State": {"Name": "running"}}]}]}
        if args[:2] == ("ssm", "send-command"):
            requests.append(kwargs["payload"])
            return {"Command": {"CommandId": "command"}}
        return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "secret"}

    assert send(aws, config, "i-fixture", ["true"]) == "command"
    assert requests[0]["InstanceIds"] == ["i-fixture"]
    assert "secret" not in json.dumps(status(aws, "command", "i-fixture"))
    with pytest.raises(ValueError):
        send(aws, config, "i-fixture", [], document="arbitrary")
    tags[0]["Value"] = "yejin"
    with pytest.raises(ValueError):
        send(aws, config, "i-fixture", ["true"])
    assert len(requests) == 1


@pytest.mark.parametrize("branch", ["main", "ikonly", "untrusted"])
def test_publication_rejects_failed_or_untrusted_ci_before_loading_images(tmp_path, branch):
    from infra.aws.publish_release import verify

    candidate = {"version": 1, "source_sha": "a" * 40, "ci_run_id": "123", "ci_attempt": 1}
    (tmp_path / "candidate.json").write_text(json.dumps(candidate))
    (tmp_path / "ci-verified.json").write_text(json.dumps({**candidate, "ci_verified": True}))
    run_metadata = {
        "head_branch": branch,
        "event": "push",
        "path": ".github/workflows/ikonly-ci.yml",
        "status": "completed",
        "conclusion": "failure",
        "head_sha": "a" * 40,
        "id": 123,
        "run_attempt": 1,
        "head_repository": {"full_name": "ik-e/multi-agent-autohealing"},
    }
    with pytest.raises(ValueError, match="provenance"):
        verify(tmp_path, run_metadata, [])
    run_metadata["conclusion"] = "success"
    if branch != "main":
        with pytest.raises(ValueError, match="provenance"):
            verify(tmp_path, run_metadata, [{"name": "release-gate", "conclusion": "success"}])
        return
    with pytest.raises(ValueError, match="remote CI job"):
        verify(tmp_path, run_metadata, [{"name": "release-gate", "conclusion": "success"}])


def test_main_workflow_and_oidc_branch_guards_are_consistent():
    import ast
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[3]
    for name in ("ikonly-ci.yml", "ikonly-infrastructure.yml", "ikonly-lifecycle.yml"):
        text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        config = yaml.load(text, Loader=yaml.BaseLoader)
        assert config["on"]["push"]["branches"] == ["main"]
        assert "refs/heads/ikonly" not in text
    ci = (root / ".github/workflows/ikonly-ci.yml").read_text(encoding="utf-8")
    lifecycle = (root / ".github/workflows/ikonly-lifecycle.yml").read_text(encoding="utf-8")
    assert 'test "$GITHUB_REF" = refs/heads/main' in ci
    assert "github.ref == 'refs/heads/main'" in lifecycle
    assert "run['head_branch'] != 'main'" in lifecycle
    steps = yaml.load(lifecycle, Loader=yaml.BaseLoader)["jobs"]["lifecycle"]["steps"]
    gate_step = next(
        step
        for step in steps
        if step.get("name") == "Validate inputs and retrieve exact verified CI artifacts"
    )
    source = gate_step["run"].split("\n", 1)[1].rsplit("\nPY", 1)[0]
    source_gate = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.If)
        and "changed" in {name.id for name in ast.walk(node.test) if isinstance(name, ast.Name)}
        and any(
            isinstance(child, ast.Constant)
            and child.value == "CI source differs from current executable source"
            for child in ast.walk(node)
        )
    )
    expression = compile(ast.Expression(source_gate.test), "source-gate", "eval")
    for changed, blocked in (
        (["README.md"], False),
        (["docs/ik/result.json", "docs/ik/README.md", ""], False),
        (["backend/app/main.py"], True),
        (["README.md", ".github/workflows/ikonly-ci.yml"], True),
        (["infra/aws/publish_release.py"], True),
    ):
        assert eval(expression, {"changed": changed}) is blocked
    for name in ("publish_release.py", "bootstrap_oidc.py"):
        text = (root / "infra/aws" / name).read_text(encoding="utf-8")
        assert "refs/heads/main" in text
        assert "refs/heads/ikonly" not in text


def test_archive_identity_accepts_config_and_manifest_ids_without_rebuild(tmp_path):
    import io
    import tarfile

    from infra.aws.publish_release import archive_identity

    sha = "a" * 40
    config = json.dumps(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {"Labels": {"org.opencontainers.image.revision": sha}},
        }
    ).encode()
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = json.dumps({"config": {"digest": config_digest}}).encode()
    manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    metadata = {"tag": "ikonly-application:ci", "image_id": config_digest}
    members = {
        "manifest.json": json.dumps(
            [{"Config": "config.json", "RepoTags": [metadata["tag"]]}]
        ).encode(),
        "config.json": config,
        "index.json": json.dumps({"manifests": [{"digest": manifest_digest}]}).encode(),
        "blobs/sha256/" + manifest_digest.split(":")[1]: manifest,
    }
    path = tmp_path / "application.tar"
    with tarfile.open(path, "w") as archive:
        for name, content in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    assert archive_identity(path, metadata, sha) == {config_digest, manifest_digest}
    with pytest.raises(ValueError):
        archive_identity(path, {**metadata, "image_id": "sha256:" + "b" * 64}, sha)


def test_compose_web_tmpfs_is_one_mount_and_ca_volumes_are_persistent():
    import yaml
    from infra.aws.iac import ROOT

    web = yaml.safe_load((ROOT / "compose.web.yml").read_text())["services"]["web"]
    assert web["tmpfs"] == ["/tmp:size=32m,mode=1777"]
    assert web["read_only"] is True
    assert "/var/lib/eventquest/caddy-data:/data" in web["volumes"]
    assert "/var/lib/eventquest/caddy-config:/config" in web["volumes"]
