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
        "test-results/lifecycle/result/result.json\ntest-results/lifecycle/result/residual-check.json\ntest-results/lifecycle/supply-cleanup/cleanup.json\n",
    ]
    assert "id-token" in config["jobs"]["lifecycle"]["permissions"]
    # NUL-delimited Git paths preserve Korean names and embedded newlines.
    validation = next(
        step["run"] for step in steps if step.get("name", "").startswith("Validate inputs")
    )
    assert "'--name-only','-z'" in validation
    assert ".decode('utf-8').split('\\0')" in validation


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


class SupplyStore:
    def __init__(self):
        self.values = {}
        self.fail_put_at = None
        self.writes = 0
        self.fail_delete = False

    def aws(self, *args, **kwargs):
        from infra.aws.bootstrap_supply import ROOT

        if args[:2] == ("ssm", "get-parameters"):
            return {
                "Parameters": [
                    {"Name": name, "Value": self.values[name]}
                    for name in kwargs["payload"]["Names"]
                    if name in self.values
                ]
            }
        if args[:2] == ("ssm", "describe-parameters"):
            prefix = args[-1].split("Values=", 1)[1]
            return {
                "Parameters": [{"Name": name} for name in self.values if name.startswith(prefix)]
            }
        if args[:2] == ("ssm", "delete-parameter"):
            if self.fail_delete:
                raise RuntimeError("simulated deletion failure")
            self.values.pop(args[-1], None)
            return {}
        pytest.fail("Unexpected SSM operation under " + ROOT)

    def put(self, aws, name, value, *, secret=False):
        from infra.aws.bootstrap_supply import ROOT

        self.writes += 1
        if self.writes == self.fail_put_at:
            raise RuntimeError("simulated registration failure")
        self.values[ROOT + name.removeprefix("bootstrap/")] = value

    def get(self, aws, name, *, secret=False):
        from infra.aws.bootstrap_supply import ROOT

        return self.values[ROOT + name.removeprefix("bootstrap/")]


def test_failed_generation_preserves_previous_active_and_removes_partial(tmp_path):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    previous = supply.register(store.aws, store.put, store.get, "original", "config", tmp_path)
    before = dict(store.values)
    store.fail_put_at = store.writes + 2
    with pytest.raises(RuntimeError, match="registration failure"):
        supply.register(store.aws, store.put, store.get, "a" * 8000, "config", tmp_path)
    assert store.values == before
    assert supply.optional(store.aws, "active") == previous


def test_shorter_registration_removes_all_old_chunks(tmp_path):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    previous = supply.register(store.aws, store.put, store.get, "a" * 9000, "config", tmp_path)
    current = supply.register(store.aws, store.put, store.get, "small", "config", tmp_path)
    assert current != previous
    assert not supply.names(store.aws, previous + "/")
    assert len(supply.names(store.aws, current + "/")) == 2  # one chunk + manifest


def test_expired_and_orphan_generations_are_swept(tmp_path, monkeypatch):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    expired = supply.register(store.aws, store.put, store.get, "test", "config", tmp_path)
    manifest = supply.manifest(store.aws, expired)
    manifest["expires_at"] = 0
    store.put(store.aws, f"bootstrap/{expired}/manifest", json.dumps(manifest))
    store.values[supply.ROOT + "a" * 32 + "/0"] = "orphan-secret"
    supply.sweep(store.aws)
    assert store.values == {}


def test_running_consumer_prevents_replacement_and_sweep(tmp_path, monkeypatch):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    value = supply.register(store.aws, store.put, store.get, "test", "config", tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    supply.claim(store.aws, store.put, tmp_path)
    monkeypatch.setattr(
        supply, "running_owner", lambda record: bool(record and record.get("run_id"))
    )
    before = dict(store.values)
    with pytest.raises(ValueError, match="running workflow"):
        supply.register(store.aws, store.put, store.get, "new", "config", tmp_path)
    assert store.values == before
    assert supply.optional(store.aws, "active") == value


@pytest.mark.parametrize("failure", ["hash", "config", "expired", "owner"])
def test_consumption_validation_failures_clean_only_owned_generation(
    tmp_path, monkeypatch, failure
):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    value = supply.register(store.aws, store.put, store.get, "test", "config", tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    supply.claim(store.aws, store.put, tmp_path)
    record = supply.manifest(store.aws, value)
    config = "wrong" if failure == "config" else "config"
    if failure == "hash":
        store.values[supply.ROOT + value + "/0"] = "corrupt"
    if failure == "expired":
        record["expires_at"] = 0
    if failure == "owner":
        monkeypatch.setenv("GITHUB_RUN_ID", "other")
    store.put(store.aws, f"bootstrap/{value}/manifest", json.dumps(record))
    try:
        with pytest.raises(ValueError):
            supply.consume(store.aws, store.get, tmp_path / "claim.json", config)
    finally:
        supply.remove(store.aws, value, owner="123", output=tmp_path)
    assert store.values == {}
    assert json.loads((tmp_path / "cleanup.json").read_text())["remaining_count"] == 0


def test_early_workflow_failure_cleans_claimed_generation(tmp_path, monkeypatch):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    value = supply.register(store.aws, store.put, store.get, "test", "config", tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    supply.claim(store.aws, store.put, tmp_path)
    # No payload consumption/paid initial is needed for the always cleanup.
    supply.remove(store.aws, value, owner="123", output=tmp_path)
    assert not store.values


def test_cleanup_failure_is_visible_and_retryable(tmp_path, monkeypatch):
    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    value = supply.register(store.aws, store.put, store.get, "secret-test", "config", tmp_path)
    store.fail_delete = True
    with pytest.raises(RuntimeError, match="deletion failure"):
        supply.remove(store.aws, value, output=tmp_path)
    metadata = json.loads((tmp_path / "cleanup.json").read_text())
    assert metadata["status"] == "CLEANUP_FAILED" and metadata["remaining_count"] > 0
    assert "secret-test" not in json.dumps(metadata)
    store.fail_delete = False
    supply.remove(store.aws, value, output=tmp_path)
    assert not store.values


@pytest.mark.parametrize("failed", [False, True])
def test_first_deployment_success_or_failure_removes_owned_supply(tmp_path, monkeypatch, failed):
    import contextlib
    import hashlib
    import time

    from infra.aws import bootstrap_supply as supply

    store = SupplyStore()
    config = {"test": "contract"}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    payload = {
        "env": {"OPENAI_API_KEY": "dummy-not-a-real-key"},
        "policy_text": "{}",
        "policy_hash": hashlib.sha256(b"{}").hexdigest(),
        "config_hash": config_hash,
        "expires_at": int(time.time()) + 100,
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    value = supply.register(store.aws, store.put, store.get, encoded, config_hash, tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    supply.claim(store.aws, store.put, tmp_path)
    _, public = keypair(tmp_path)
    monkeypatch.setattr(gate, "get", store.get)
    monkeypatch.setattr(gate, "operation_lock", lambda *args: contextlib.nullcontext())

    def invoke(*args, **kwargs):
        assert kwargs["lock_held"] is True
        if failed:
            raise RuntimeError("simulated paid-operation failure")

    monkeypatch.setattr(gate, "invoke", invoke)
    args = argparse.Namespace(
        claim_file=tmp_path / "claim.json",
        output=tmp_path / "result",
        encrypted_output=tmp_path / "evidence.bin",
        directory=tmp_path,
        run_id=123,
    )
    if failed:
        with pytest.raises(RuntimeError, match="paid-operation failure"):
            gate.first_deployment(store.aws, config, args, public, [])
    else:
        gate.first_deployment(store.aws, config, args, public, [])
    assert not supply.names(store.aws, value + "/")
    assert supply.optional(store.aws, "active") is None
    assert args.encrypted_output.exists()
