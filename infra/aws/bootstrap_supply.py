"""Atomic generations of temporary bootstrap secrets; no secret values in metadata."""

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from infra.aws.cleanup import pages
from infra.aws.host_release import run
from infra.aws.operation_state import write

ROOT = "/multi-agent-autohealing/control/ikonly-demo/lifecycle/bootstrap/"
GENERATION = re.compile(r"[0-9a-f]{32}\Z")


def optional(aws, name):
    values = aws(
        "ssm", "get-parameters", payload={"Names": [ROOT + name], "WithDecryption": False}
    ).get("Parameters", [])
    return values[0]["Value"] if values else None


def names(aws, prefix):
    return [
        item["Name"]
        for item in pages(
            aws,
            (
                "ssm",
                "describe-parameters",
                "--parameter-filters",
                "Key=Name,Option=BeginsWith,Values=" + ROOT + prefix,
            ),
            "Parameters",
        )
    ]


def generation(value):
    if not GENERATION.fullmatch(value or ""):
        raise ValueError("Invalid bootstrap generation")
    return value


def manifest(aws, value):
    raw = optional(aws, generation(value) + "/manifest")
    return json.loads(raw) if raw else None


def running_owner(record):
    owner = record and record.get("run_id")
    if not owner:
        return False
    metadata = json.loads(
        run(["gh", "api", "repos/ik-e/multi-agent-autohealing/actions/runs/" + str(owner)])
    )
    return metadata["status"] != "completed"


def remove(aws, value, *, owner=None, output=None):
    value = generation(value)
    record = manifest(aws, value)
    if (
        record
        and record.get("run_id")
        and record["run_id"] != owner
        and (owner is not None or running_owner(record))
    ):
        raise ValueError("Another workflow owns this supply generation")
    try:
        for name in names(aws, value + "/"):
            aws("ssm", "delete-parameter", "--name", name)
        if optional(aws, "active") == value:
            aws("ssm", "delete-parameter", "--name", ROOT + "active")
    except Exception:
        if output:
            write(
                Path(output) / "cleanup.json",
                {
                    "status": "CLEANUP_FAILED",
                    "generation": value,
                    "remaining_count": len(names(aws, value + "/")),
                },
            )
        raise
    remaining = names(aws, value + "/")
    if output:
        write(
            Path(output) / "cleanup.json",
            {"status": "SUPPLY_REMOVED", "generation": value, "remaining_count": len(remaining)},
        )
    if remaining:
        raise ValueError("Supply cleanup still has remaining parameters")


def sweep(aws, *, clear_active=False):
    """Called under the lifecycle lock; refuse cleanup of any active consumer."""
    active = optional(aws, "active")
    listed = names(aws, "")
    generations = {
        name.removeprefix(ROOT).split("/")[0]
        for name in listed
        if GENERATION.fullmatch(name.removeprefix(ROOT).split("/")[0])
    }
    records = {value: manifest(aws, value) for value in generations}
    if any(running_owner(record) for record in records.values()):
        raise ValueError("Bootstrap supply is claimed by a running workflow")
    for value, record in records.items():
        if (
            value != active
            or clear_active
            or not record
            or record.get("expires_at", 0) < time.time()
        ):
            remove(aws, value)
    # Reclaim old protocol count/chunk names, which have no generation manifest.
    for name in listed:
        suffix = name.removeprefix(ROOT)
        if suffix != "active" and not GENERATION.fullmatch(suffix.split("/")[0]):
            aws("ssm", "delete-parameter", "--name", name)
    if active and active not in generations:
        aws("ssm", "delete-parameter", "--name", ROOT + "active")


def register(aws, put, get, encoded, config_hash, output):
    sweep(aws)
    previous = optional(aws, "active")
    value = uuid.uuid4().hex
    chunks = [encoded[i : i + 3500] for i in range(0, len(encoded), 3500)]
    if not 1 <= len(chunks) <= 32:
        raise ValueError("Supply exceeds Standard Parameter chunk limit")
    try:
        for index, chunk in enumerate(chunks):
            put(aws, f"bootstrap/{value}/{index}", chunk, secret=True)
        sha = hashlib.sha256(encoded.encode()).hexdigest()
        verified = "".join(
            get(aws, f"bootstrap/{value}/{i}", secret=True) for i in range(len(chunks))
        )
        if hashlib.sha256(verified.encode()).hexdigest() != sha:
            raise ValueError("Supply write verification failed")
        record = {
            "generation": value,
            "count": len(chunks),
            "payload_sha256": sha,
            "config_hash": config_hash,
            "expires_at": int(time.time()) + 86400,
            "run_id": None,
        }
        put(aws, f"bootstrap/{value}/manifest", json.dumps(record))
        put(aws, "bootstrap/active", value)
    except Exception:
        if previous and optional(aws, "active") == value:
            put(aws, "bootstrap/active", previous)
        remove(aws, value, output=output)
        raise
    sweep(aws)
    write(
        Path(output) / "supply.json",
        {
            "status": "SUPPLIED",
            "generation": value,
            "chunk_count": len(chunks),
            "expires_at": record["expires_at"],
        },
    )
    return value


def claim(aws, put, output):
    value = generation(optional(aws, "active"))
    record = manifest(aws, value)
    owner = os.environ["GITHUB_RUN_ID"]
    if not record or record.get("expires_at", 0) < time.time():
        remove(aws, value, output=output)
        raise ValueError("Supply expired or incomplete; supply again")
    if record.get("run_id") not in (None, owner):
        raise ValueError("Supply already claimed by another workflow")
    record["run_id"] = owner
    put(aws, f"bootstrap/{value}/manifest", json.dumps(record))
    write(Path(output) / "claim.json", {"generation": value, "run_id": owner})
    return value


def consume(aws, get, claim_file, config_hash):
    claimed = json.loads(Path(claim_file).read_text())
    value = generation(claimed["generation"])
    record = manifest(aws, value)
    if (
        not record
        or claimed["run_id"] != os.environ["GITHUB_RUN_ID"]
        or record.get("run_id") != claimed["run_id"]
    ):
        raise ValueError("Workflow does not own the claimed generation")
    if record.get("expires_at", 0) < time.time() or record["config_hash"] != config_hash:
        raise ValueError("Supply expired or config changed")
    count = record["count"]
    if not isinstance(count, int) or not 1 <= count <= 32:
        raise ValueError("Invalid supply manifest count")
    encoded = "".join(get(aws, f"bootstrap/{value}/{i}", secret=True) for i in range(count))
    if hashlib.sha256(encoded.encode()).hexdigest() != record["payload_sha256"]:
        raise ValueError("Supply payload changed")
    return encoded
