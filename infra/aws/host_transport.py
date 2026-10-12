"""Bounded SSM operations and non-secret, hash-verified host payloads."""

import base64
import gzip
import hashlib
import io
import json
import time
from pathlib import Path
from shlex import quote

from infra.aws.operation_state import write
from infra.aws.ssm_operations import send, status


def command(
    aws, config, instance, script, output, *, document="AWS-RunShellScript", parameters=None
):
    identifier = send(aws, config, instance, [script], document=document, parameters=parameters)
    record = {"command_id": identifier, "instance_id": instance}
    write(output, record)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        try:
            result = status(aws, identifier, instance)
        except RuntimeError:
            # Invocation metadata may not be visible immediately after SendCommand.
            time.sleep(3)
            continue
        record.update(result)
        write(output, record)
        if result["Status"] == "Success":
            return record
        if result["Status"] not in ("Pending", "InProgress", "Delayed"):
            raise RuntimeError("SSM operation failed; inspect metadata record")
        time.sleep(5)
    raise TimeoutError("SSM operation deadline exceeded")


def stage_script(files):
    """All file paths originate in reviewed code; contents must be non-secret."""
    payload = {}
    for name, content in files.items():
        if not name.startswith(("/etc/eventquest/", "/var/lib/eventquest/releases/")):
            raise ValueError("Host payload path outside deployment directories")
        payload[name] = {
            "content": base64.b64encode(content).decode(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return (
        "set -eu\npython3 - <<'PY'\nimport base64,hashlib,os\nfrom pathlib import Path\nfiles="
        + repr(payload)
        + "\nfor name,item in files.items():\n raw=base64.b64decode(item['content']);assert hashlib.sha256(raw).hexdigest()==item['sha256']\n p=Path(name);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)\n if p.exists() and '/releases/' in name: assert p.read_bytes()==raw, 'Immutable staged file differs'\n p.write_bytes(raw);os.chmod(p,0o600)\nPY"
    )


def support_script(release_id, deployment, source_sha):
    import subprocess
    import tarfile

    stream = io.BytesIO()
    root = Path(__file__).parent
    names = (
        "iac.py",
        "infrastructure.py",
        "parameters.py",
        "host_release.py",
        "host_database.py",
        "compose.application.yml",
        "compose.web.yml",
        "compose.fault.yml",
    )
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name in names:
            content = subprocess.run(
                ["git", "show", source_sha + ":infra/aws/" + name],
                cwd=root.parents[1],
                capture_output=True,
                check=True,
            ).stdout
            info = tarfile.TarInfo("infra/aws/" + name)
            info.size, info.mode = len(content), 0o600
            archive.addfile(info, io.BytesIO(content))
    blob = gzip.compress(stream.getvalue(), mtime=0)
    encoded = base64.b64encode(blob).decode()
    sha = hashlib.sha256(blob).hexdigest()
    directory = "/var/lib/eventquest/releases/" + release_id + "/support"
    return (
        "set -eu\nfor n in $(seq 1 120); do test -f /etc/eventquest/host-bootstrap-complete && break; sleep 5; done\ntest -f /etc/eventquest/host-bootstrap-complete\npython3 - <<'PY'\nimport base64,gzip,hashlib,io,tarfile\nfrom pathlib import Path\nblob=base64.b64decode("
        + repr(encoded)
        + ");assert hashlib.sha256(blob).hexdigest()=="
        + repr(sha)
        + "\nroot=Path("
        + repr(directory)
        + ");root.mkdir(parents=True,exist_ok=True,mode=0o700)\nwith tarfile.open(fileobj=io.BytesIO(gzip.decompress(blob))) as archive:\n for item in archive.getmembers(): assert item.isfile() and (root/item.name).resolve().is_relative_to(root)\n archive.extractall(root,filter='data')\nPY\n"
        + stage_script({"/etc/eventquest/deployment.json": json.dumps(deployment).encode()})
        + '\ncat > /opt/eventquest/bin/deploy-release <<\'SH\'\n#!/bin/sh\nset -eu\ncase "$1" in *[!a-f0-9]*|\'\') exit 2;; esac\ntest "${#1}" = 40\nexport PYTHONPATH=/var/lib/eventquest/releases/$1/support\nexec python3 -m infra.aws.host_release "$@"\nSH\nchmod 0700 /opt/eventquest/bin/deploy-release'
    )


def db_script(release, account, endpoint, action):
    image = (
        account
        + ".dkr.ecr.ap-northeast-2.amazonaws.com/ikonly-demo/application@"
        + release["services"]["main-api"]["digest"]
    )
    source = (
        release["services"]["main-api"].get("deployment_id")
        or release["services"]["main-api"]["source_sha"]
    )
    return (
        "set -eu\nexport PYTHONPATH="
        + quote("/var/lib/eventquest/releases/" + source + "/support")
        + "\npython3 -m infra.aws.host_database "
        + quote(action)
        + " --image "
        + quote(image)
        + " --endpoint "
        + quote(endpoint)
        + " --release-id "
        + quote(release.get("release_id") or release["source_sha"])
    )
