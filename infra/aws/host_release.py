"""Root-only, hash-bound host release application invoked by IkonlyDeploy."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from infra.aws.iac import ROLES
from infra.aws.infrastructure import Aws
from infra.aws.parameters import main as load_parameters


def selected_image(manifest, role, account, release_id, manifest_hash, raw):
    if hashlib.sha256(raw).hexdigest() != manifest_hash:
        raise ValueError("Release manifest hash mismatch")
    if (
        manifest.get("environment_id") != "ikonly-demo"
        or (manifest.get("release_id") or manifest.get("source_sha")) != release_id
        or not re.fullmatch(r"[a-f0-9]{40}", release_id)
        or set(manifest.get("services", {})) != set(ROLES)
        or not re.fullmatch(r"\d{12}", account)
    ):
        raise ValueError("Release scope is invalid")
    service = manifest["services"][role]
    image = "web" if role.endswith("-web") else "application"
    if (
        service.get("image") != image
        or not re.fullmatch(r"[a-f0-9]{40}", service.get("source_sha", ""))
        or (service.get("deployment_id") or service.get("source_sha")) != release_id
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", service.get("digest", ""))
    ):
        raise ValueError("Service image identity is invalid")
    return f"{account}.dkr.ecr.ap-northeast-2.amazonaws.com/ikonly-demo/{image}@{service['digest']}"


def run(args, *, input_text=None, environment=None):
    result = subprocess.run(
        args, input=input_text, env=environment, capture_output=True, text=True, check=False
    )
    if result.returncode:
        # Docker/Compose errors can include env values. Keep diagnostics metadata only.
        raise RuntimeError("Host command failed: " + args[0])
    return result.stdout.strip()


def apply_release(release_id, manifest_hash):
    import fcntl

    if os.geteuid() != 0:
        raise ValueError("Host deployment requires root")
    if not re.fullmatch(r"[a-f0-9]{40}", release_id) or not re.fullmatch(
        r"[a-f0-9]{64}", manifest_hash
    ):
        raise ValueError("Invalid release arguments")
    with Path("/etc/eventquest/deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = Path("/var/lib/eventquest/releases") / release_id
        raw = (directory / "release.json").read_bytes()
        manifest = json.loads(raw)
        role = Path("/etc/eventquest/service-role").read_text().strip()
        host = json.loads(Path("/etc/eventquest/deployment.json").read_text())
        identity = Aws("ap-northeast-2")("sts", "get-caller-identity")
        if identity["Account"] != host["account_id"]:
            raise ValueError("Host account mismatch")
        image = selected_image(manifest, role, host["account_id"], release_id, manifest_hash, raw)
        config = directory / "host-config.json"
        previous_env = directory / "previous.env"
        runtime = Path("/etc/eventquest/runtime.env")
        if runtime.exists() and not previous_env.exists():
            shutil.copyfile(runtime, previous_env)
            previous_env.chmod(0o600)
        import sys

        previous_argv = sys.argv
        try:
            sys.argv = ["load-parameters", "--config", str(config)]
            load_parameters()
        finally:
            sys.argv = previous_argv
        registry = image.split("/", 1)[0]
        token = run(["aws", "--region", "ap-northeast-2", "ecr", "get-login-password"])
        try:
            run(
                ["docker", "login", "--username", "AWS", "--password-stdin", registry],
                input_text=token,
            )
            run(["docker", "pull", image])
        finally:
            run(["docker", "logout", registry])
        revision = run(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            ]
        )
        if revision != manifest["services"][role]["source_sha"]:
            raise ValueError("Image source revision mismatch")
        support = Path(__file__).parent
        web = role.endswith("-web")
        env = {
            **os.environ,
            "WEB_IMAGE" if web else "APPLICATION_IMAGE": image,
            "WEB_ENV_FILE" if web else "APPLICATION_ENV_FILE": str(runtime),
            "PRIVATE_BIND_IP": host["private_ip"],
            "SERVICE_PORT": "8010" if role.endswith("-mcp") else "8000",
        }
        command = [
            "docker",
            "compose",
            "--project-name",
            "eventquest",
            "-f",
            str(support / ("compose.web.yml" if web else "compose.application.yml")),
        ]
        if role == "fault-api":
            command += ["-f", str(support / "compose.fault.yml")]
        run([*command, "up", "--detach", "--no-build", "--pull", "never"], environment=env)
        service_name = "web" if web else "application"
        container = run([*command, "ps", "--quiet", service_name], environment=env)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            health = run(["docker", "inspect", "--format", "{{.State.Health.Status}}", container])
            if health == "healthy":
                (directory / "accepted.json").write_text(
                    json.dumps(
                        {
                            "role": role,
                            "release_id": release_id,
                            "image": image,
                            "manifest_sha256": manifest_hash,
                        }
                    )
                )
                print("Host service health accepted: " + role)
                return
            if health == "unhealthy":
                break
            time.sleep(3)
        raise RuntimeError("Host readiness failed; fleet acceptance must remain blocked")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("release_id")
    parser.add_argument("manifest_sha256")
    args = parser.parse_args()
    try:
        apply_release(args.release_id, args.manifest_sha256)
    except Exception:  # noqa: BLE001 - suppress credential-bearing third-party failures.
        raise SystemExit("Host deployment failed; fleet acceptance remains blocked") from None


if __name__ == "__main__":
    main()
