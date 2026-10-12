"""Lifecycle evidence, confirmation and hosted execution boundaries."""

import argparse
import base64
import json

import pytest
import yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from infra.aws import cleanup
from infra.aws import lifecycle_dispatch as gate


def keypair(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = tmp_path / "private.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


def test_evidence_round_trip_and_no_plaintext_upload(tmp_path):
    private, public = keypair(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "web-login.txt").write_text("secret-test-password")
    (source / "journal.json").write_text('{"raw":"prompt-test"}')
    sealed = tmp_path / "evidence.bin"
    sha = gate.seal(source, public, sealed)
    assert b"secret-test-password" not in sealed.read_bytes()
    assert b"prompt-test" not in sealed.read_bytes()
    assert gate.unseal(sealed, private, tmp_path / "local") == sha
    assert (tmp_path / "local/web-login.txt").read_text() == "secret-test-password"


def test_evidence_tampering_is_rejected(tmp_path):
    private, public = keypair(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_text("test")
    sealed = tmp_path / "evidence.bin"
    gate.seal(source, public, sealed)
    raw = bytearray(sealed.read_bytes())
    raw[-1] ^= 1
    sealed.write_bytes(raw)
    with pytest.raises(InvalidTag):
        gate.unseal(sealed, private, tmp_path / "local")


def test_wrong_private_key_is_rejected(tmp_path):
    private, public = keypair(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    sealed = tmp_path / "evidence.bin"
    gate.seal(source, public, sealed)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(
        other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError):
        gate.unseal(sealed, private, tmp_path / "local")


@pytest.mark.parametrize(
    "receipt, input_hash",
    [
        ({"sha256": "a" * 64, "release_id": "old"}, "a" * 64),
        ({"sha256": "a" * 64, "release_id": "current"}, "b" * 64),
    ],
)
def test_reset_refuses_wrong_release_or_evidence(monkeypatch, receipt, input_hash):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        gate,
        "preflight",
        lambda *a: {"principal_arn": "arn:aws:sts::1:assumed-role/ikonly-control-controller/test"},
    )
    monkeypatch.setattr(
        gate,
        "get",
        lambda aws, name: "public" if name == "evidence-public-key" else json.dumps(receipt),
    )
    monkeypatch.setattr(
        gate,
        "read_state",
        lambda aws: {
            "release": {"release_id": "current"},
            "reset": {"encrypted_evidence_sha256": "a" * 64},
        },
    )
    monkeypatch.setattr(gate, "invoke", lambda *a: pytest.fail("Must not delete"))
    args = argparse.Namespace(
        operation="reset",
        confirm_environment="ikonly-demo",
        output="ignored",
        config="ignored",
        evidence_sha256=input_hash,
    )
    with pytest.raises(ValueError, match="verified local evidence"):
        gate.dispatch(object(), {}, args)


def test_prepare_reset_collects_evidence_without_deleting(tmp_path, monkeypatch):
    calls = []

    def aws(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("cloudformation", "describe-stacks"):
            return {"Stacks": []}
        if args[:2] == ("s3api", "list-buckets"):
            return {"Buckets": []}
        if args[:2] == ("logs", "describe-log-groups"):
            return {"logGroups": []}
        pytest.fail("Unexpected mutation: " + str(args))

    monkeypatch.setattr(cleanup, "journal", lambda *a: None)
    cleanup.reset(aws, {}, None, tmp_path, prepare_only=True)
    assert json.loads((tmp_path / "execution.json").read_text())["status"] == "EVIDENCE_DOWNLOADED"
    assert not any("delete" in str(args) for args in calls)


def test_workflow_only_uploads_encrypted_and_safe_outputs():
    from pathlib import Path

    config = yaml.safe_load(Path(".github/workflows/ikonly-lifecycle.yml").read_text())
    steps = config["jobs"]["lifecycle"]["steps"]
    uploads = [step["with"]["path"] for step in steps if "upload-artifact" in step.get("uses", "")]
    assert uploads == [
        "test-results/lifecycle/encrypted/",
        "test-results/lifecycle/result/result.json\ntest-results/lifecycle/result/residual-check.json\n",
    ]
    assert "id-token" in config["jobs"]["lifecycle"]["permissions"]


def test_parameter_secret_is_not_passed_in_cli_argv():
    calls = []

    def aws(*args, **kwargs):
        from pathlib import Path

        if kwargs:
            assert kwargs["payload"]["ResourceType"] == "Parameter"
            return
        request = json.loads(Path(args[-1].removeprefix("file://")).read_text())
        calls.append((args, request))

    gate.put(aws, "bootstrap/0", base64.b64encode(b"secret-test").decode(), secret=True)
    assert calls[0][1]["Type"] == "SecureString"
    assert calls[0][1]["Value"] not in str(calls[0][0])
