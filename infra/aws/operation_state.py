"""Environment-wide operations lock and accepted release state in Standard SSM."""

import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from infra.aws.iac import PREFIX

LOCK = "/multi-agent-autohealing/control/ikonly-demo/lock"
STATE = PREFIX + "deployment/accepted"


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_state(aws):
    response = aws("ssm", "get-parameters", payload={"Names": [STATE], "WithDecryption": False})
    values = response.get("Parameters", [])
    if not values:
        return None
    if len(values) != 1 or response.get("InvalidParameters"):
        raise ValueError("Deployment state is ambiguous")
    return json.loads(values[0]["Value"])


def commit_state(aws, state):
    raw = json.dumps(state, separators=(",", ":"))
    if len(raw.encode()) > 4096:
        raise ValueError("Deployment state exceeds Standard Parameter size")
    existing = aws("ssm", "get-parameters", payload={"Names": [STATE], "WithDecryption": False})
    request = {
        "Name": STATE,
        "Value": raw,
        "Type": "String",
        "Tier": "Standard",
        "Overwrite": bool(existing.get("Parameters")),
    }
    if not request["Overwrite"]:
        request["Tags"] = [
            {"Key": "Environment", "Value": "ikonly-demo"},
            {"Key": "ManagedBy", "Value": "ikonly-iac"},
        ]
    aws("ssm", "put-parameter", payload=request)


@contextmanager
def operation_lock(aws, operation, output):
    token = str(uuid.uuid4())
    value = json.dumps(
        {
            "token": token,
            "operation": operation,
            "started_at": datetime.now(UTC).isoformat(),
            "github_run_id": os.getenv("GITHUB_RUN_ID"),
        }
    )
    # Overwrite=False is the cross-runner acquisition primitive. No lease stealing:
    # after a killed runner an operator must verify it ended before clearing the lock.
    aws(
        "ssm",
        "put-parameter",
        payload={
            "Name": LOCK,
            "Value": value,
            "Type": "String",
            "Tier": "Standard",
            "Overwrite": False,
        },
    )
    write(Path(output) / "lock.json", json.loads(value))
    try:
        yield
    finally:
        response = aws("ssm", "get-parameters", payload={"Names": [LOCK], "WithDecryption": False})
        parameters = response.get("Parameters", [])
        if len(parameters) == 1 and json.loads(parameters[0]["Value"]).get("token") == token:
            aws("ssm", "delete-parameter", "--name", LOCK)
