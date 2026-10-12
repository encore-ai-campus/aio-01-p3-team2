"""Hosted Actions lifecycle gate with local key supply and encrypted evidence."""

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values
from infra.aws.infrastructure import Aws, preflight, validate_config
from infra.aws.operation_state import commit_state, operation_lock, read_state, write

CONTROL = "/multi-agent-autohealing/control/ikonly-demo/lifecycle/"
ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPERATOR_ACTOR_ID",
    "APPROVED_ACTOR_IDS",
    "OPENAI_MODEL",
    "LLM_MAX_INPUT_TOKENS",
    "LLM_MAX_OUTPUT_TOKENS",
    "LLM_MAX_COST_USD",
    "LLM_INPUT_USD_PER_MILLION",
    "LLM_OUTPUT_USD_PER_MILLION",
    "LLM_PRICING_VERSION",
    "LLM_COST_CURRENCY",
)


def put(aws, name, value, *, secret=False):
    # Secrets are provided in a private file, never CLI argv or Actions output.
    request = {
        "Name": CONTROL + name,
        "Value": value,
        "Type": "SecureString" if secret else "String",
        "Tier": "Standard",
        "Overwrite": True,
    }
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "parameter.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        path.chmod(0o600)
        aws("ssm", "put-parameter", "--cli-input-json", "file://" + str(path))
    aws(
        "ssm",
        "add-tags-to-resource",
        payload={
            "ResourceType": "Parameter",
            "ResourceId": CONTROL + name,
            "Tags": [
                {"Key": "Environment", "Value": "ikonly-demo"},
                {"Key": "ManagedBy", "Value": "ikonly-iac"},
            ],
        },
    )


def get(aws, name, *, secret=False):
    return aws("ssm", "get-parameter", payload={"Name": CONTROL + name, "WithDecryption": secret})[
        "Parameter"
    ]["Value"]


def public_key(aws, private_path):
    private_path = Path(private_path)
    if not private_path.exists():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        private_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        private_path.chmod(0o600)
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    put(aws, "evidence-public-key", pem)


def seal(folder, pem, target):
    """Only RSA/AES-GCM encrypted bytes may be uploaded to GitHub."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for path in sorted(Path(folder).rglob("*")):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(folder)))
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    public = serialization.load_pem_public_key(pem.encode())
    wrapped = public.encrypt(
        key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    )
    header = {
        "version": 1,
        "key": base64.b64encode(wrapped).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }
    aad = json.dumps(header, sort_keys=True).encode()
    raw = aad + b"\n" + AESGCM(key).encrypt(nonce, data.getvalue(), aad)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def unseal(source, private_path, output):
    raw = Path(source).read_bytes()
    aad, cipher = raw.split(b"\n", 1)
    header = json.loads(aad)
    if header["version"] != 1:
        raise ValueError("Unknown evidence envelope")
    private = serialization.load_pem_private_key(Path(private_path).read_bytes(), password=None)
    key = private.decrypt(
        base64.b64decode(header["key"]),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    plain = AESGCM(key).decrypt(base64.b64decode(header["nonce"]), cipher, aad)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        for item in tar.getmembers():
            dest = (output / item.name).resolve()
            if not dest.is_relative_to(output) or not item.isfile():
                raise ValueError("Unsafe evidence archive entry")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(item).read())
            dest.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def stage(aws, config, args):
    if read_state(aws):
        raise ValueError("Stage first-deployment keys only for an absent environment")
    from backend.app.services.recovery_policy import validate_policy

    env = {name: dotenv_values(args.env_file).get(name) for name in ENV_KEYS}
    if any(not value or any(c in value for c in "\r\n\0") for value in env.values()):
        raise ValueError("Incomplete live environment")
    policy = Path(args.policy_file).read_bytes()
    if hashlib.sha256(policy).hexdigest() != args.approved_policy_sha256 or validate_policy(
        json.loads(policy)
    ):
        raise ValueError("Approved policy hash or validation failed")
    from infra.aws.bootstrap_supply import register, sweep

    sweep(aws)
    public_key(aws, args.private_key)
    raw = json.dumps(
        {
            "env": env,
            "expires_at": int(time.time()) + 86400,
            "policy_text": policy.decode("utf-8"),
            "policy_hash": args.approved_policy_sha256,
            "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        }
    )
    # Base64 makes each Standard SecureString chunk ASCII and <= 3500 bytes.
    encoded = base64.b64encode(raw.encode()).decode()
    register(
        aws,
        put,
        get,
        encoded,
        hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        args.output,
    )
    print("Local key supply complete; private evidence key stays on this PC")


def invoke(argv, *, lock_held=False):
    from infra.aws.operations import main

    sys.argv = ["operations", *argv]
    main(lock_held=lock_held)


def first_deployment(aws, config, args, pem, common):
    from infra.aws.bootstrap_supply import consume, remove

    claimed = json.loads(args.claim_file.read_text())
    if claimed["run_id"] != os.environ["GITHUB_RUN_ID"]:
        raise ValueError("Supply claim belongs to another workflow")
    folder = Path(args.output)
    with operation_lock(aws, "initial", folder):
        try:
            supplied = json.loads(
                base64.b64decode(
                    consume(
                        aws,
                        get,
                        args.claim_file,
                        hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                    )
                )
            )
            if supplied.get("expires_at", 0) < time.time():
                raise ValueError("Local key supply expired; supply it again")
            if (
                supplied["config_hash"]
                != hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            ):
                raise ValueError("Config differs from locally supplied contract")
            for value in supplied["env"].values():
                print("::add-mask::" + str(value), flush=True)
            os.environ["IKONLY_LIFECYCLE_GATE"] = "validated"
            with tempfile.TemporaryDirectory() as temp:
                env_file, policy = Path(temp) / ".env", Path(temp) / "policy.json"
                env_file.write_text(
                    "\n".join(k + "=" + json.dumps(v) for k, v in supplied["env"].items())
                )
                policy.write_bytes(supplied["policy_text"].encode("utf-8"))
                policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
                if policy_hash != supplied["policy_hash"]:
                    raise ValueError("Staged approved policy bytes changed")
                invoke(
                    [
                        "initial",
                        *common,
                        "--env-file",
                        str(env_file),
                        "--policy-file",
                        str(policy),
                        "--approved-policy-sha256",
                        policy_hash,
                        "--directory",
                        str(args.directory),
                        "--run-id",
                        str(args.run_id),
                    ],
                    lock_held=True,
                )
        finally:
            try:
                remove(
                    aws,
                    claimed["generation"],
                    owner=claimed["run_id"],
                    output=folder / "supply-cleanup",
                )
            finally:
                if folder.exists():
                    seal(folder, pem, args.encrypted_output)


def dispatch(aws, config, args):
    if os.getenv("GITHUB_ACTIONS") != "true" or args.confirm_environment != "ikonly-demo":
        raise ValueError("Hosted lifecycle requires exact environment confirmation")
    if ":assumed-role/ikonly-control-controller/" not in preflight(aws, config)["principal_arn"]:
        raise ValueError("Hosted lifecycle requires OIDC controller identity")
    pem = get(aws, "evidence-public-key")
    folder = Path(args.output)
    common = ["--config", str(args.config), "--output", str(folder)]
    if args.operation == "initial":
        first_deployment(aws, config, args, pem, common)
        return
    state = read_state(aws)
    if not state or not state.get("release"):
        raise ValueError("Reset preparation requires an accepted release")
    if args.operation == "reset":
        proof = state.get("reset", {}).get("encrypted_evidence_sha256")
        receipt = json.loads(get(aws, "local-evidence-receipt"))
        if (
            not proof
            or args.evidence_sha256 != proof
            or receipt != {"sha256": proof, "release_id": state["release"]["release_id"]}
        ):
            raise ValueError("Reset requires verified local evidence for this frozen release")
    os.environ["IKONLY_LIFECYCLE_GATE"] = "validated"
    try:
        invoke([args.operation, *common, "--confirm-environment", "ikonly-demo"])
    finally:
        if folder.exists():
            sha = seal(folder, pem, args.encrypted_output)
            write(Path(args.encrypted_output).with_suffix(".json"), {"sha256": sha})
    if args.operation == "prepare-reset":
        state = read_state(aws)
        state["reset"]["encrypted_evidence_sha256"] = sha
        commit_state(aws, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "stage",
            "register-evidence-key",
            "initial",
            "prepare-reset",
            "reset",
            "confirm-evidence",
            "unpack-evidence",
            "clear-supply",
            "claim-supply",
        ),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--approved-policy-sha256")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encrypted-output", type=Path)
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--confirm-environment")
    parser.add_argument("--claim-file", type=Path)
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    aws = Aws(config["region"])
    preflight(aws, config)
    if (
        args.operation in ("stage", "register-evidence-key", "confirm-evidence", "unpack-evidence")
        and os.getenv("GITHUB_ACTIONS") == "true"
    ):
        raise ValueError("Local-only key supply/evidence confirmation")
    if args.operation == "clear-supply":
        if args.confirm_environment != "ikonly-demo":
            raise ValueError("Supply cleanup requires exact environment confirmation")
        from infra.aws.bootstrap_supply import remove, sweep

        with operation_lock(aws, "clear-supply", args.output):
            if os.getenv("GITHUB_ACTIONS") == "true":
                claimed = json.loads(args.claim_file.read_text())
                if claimed["run_id"] != os.environ["GITHUB_RUN_ID"]:
                    raise ValueError("Cleanup claim belongs to a different workflow")
                remove(aws, claimed["generation"], owner=claimed["run_id"], output=args.output)
            else:
                sweep(aws, clear_active=True)
    elif args.operation == "stage":
        with operation_lock(aws, "stage", args.output):
            stage(aws, config, args)
    elif args.operation == "claim-supply":
        from infra.aws.bootstrap_supply import claim

        if os.getenv("GITHUB_ACTIONS") != "true" or args.confirm_environment != "ikonly-demo":
            raise ValueError("Claim requires hosted confirmed environment")
        with operation_lock(aws, "claim-supply", args.output):
            claim(aws, put, args.output)
    elif args.operation == "register-evidence-key":
        from infra.aws.bootstrap_supply import sweep

        with operation_lock(aws, "register-evidence-key", args.output):
            sweep(aws)
            public_key(aws, args.private_key)
    elif args.operation in ("confirm-evidence", "unpack-evidence"):
        sha = unseal(args.evidence_file, args.private_key, args.output)
        if args.operation == "unpack-evidence":
            print("Local decrypted evidence SHA256: " + sha)
            return
        state = read_state(aws)
        if (
            not state
            or state["lifecycle"] != "RESETTING"
            or sha != state["reset"].get("encrypted_evidence_sha256")
        ):
            raise ValueError("Local evidence does not match current frozen release")
        put(
            aws,
            "local-evidence-receipt",
            json.dumps({"sha256": sha, "release_id": state["release"]["release_id"]}),
        )
        print("Verified local evidence SHA256: " + sha)
    else:
        dispatch(aws, config, args)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - hide credential-bearing SDK exceptions.
        raise SystemExit(
            "Lifecycle gate failed; consult safe metadata, never print secret payloads"
        ) from None
