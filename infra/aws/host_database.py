"""Temporary bootstrap/migration host identity; no owner credentials reach services."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from infra.aws.host_release import run
from infra.aws.iac import PREFIX
from infra.aws.infrastructure import Aws


def operation(image, endpoint, release_id, action):
    if os.geteuid() != 0 or action not in ("initialize", "enable", "disable", "status", "drain"):
        raise ValueError("Invalid privileged database operation")
    aws = Aws("ap-northeast-2")
    expected = "bootstrap" if action == "initialize" else "migration"
    identity = aws("sts", "get-caller-identity")
    if ":assumed-role/ikonly-demo-" + expected + "/" not in identity["Arn"]:
        raise ValueError("Temporary DB host profile is not active")
    names = ["operations/main-owner-url", "operations/fault-owner-url"]
    if action == "initialize":
        names += ["operations/master-url", "operations/role-passwords"]
    response = aws(
        "ssm",
        "get-parameters",
        payload={"Names": [PREFIX + n for n in names], "WithDecryption": True},
    )
    if response.get("InvalidParameters") or len(response["Parameters"]) != len(names):
        raise ValueError("Database operation parameters are missing")
    values = {p["Name"].removeprefix(PREFIX): p["Value"] for p in response["Parameters"]}
    registry = image.split("/", 1)[0]
    if registry != identity[
        "Account"
    ] + ".dkr.ecr.ap-northeast-2.amazonaws.com" or not image.startswith(
        registry + "/ikonly-demo/application@sha256:"
    ):
        raise ValueError("Database image registry mismatch")
    token = run(["aws", "--region", "ap-northeast-2", "ecr", "get-login-password"])
    try:
        run(
            ["docker", "login", "--username", "AWS", "--password-stdin", registry], input_text=token
        )
        run(["docker", "pull", image])
    finally:
        run(["docker", "logout", registry])
    config = json.loads(Path("/etc/eventquest/database-operation.json").read_text())
    policy = Path("/etc/eventquest/approved-policy.json")
    common = {
        "DEPLOYMENT_MODE": "cloud",
        "DEPLOYMENT_ENVIRONMENT_ID": "ikonly-demo",
        "RDS_EXPECTED_ENDPOINT": endpoint,
        "OPERATOR_ACTOR_ID": config["actor"],
        "APPROVED_ACTOR_IDS": config["actor"],
    }

    def execute(module, args, extra):
        env = {**common, **extra}
        fd, filename = tempfile.mkstemp(prefix=".db-operation-", dir="/etc/eventquest")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write("\n".join(k + "=" + v for k, v in env.items()) + "\n")
            result = run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--tmpfs",
                    "/tmp",
                    "--env-file",
                    filename,
                    "--volume",
                    str(policy) + ":/tmp/approved-policy.json:ro",
                    "--entrypoint",
                    "python",
                    image,
                    "-m",
                    module,
                    *args,
                ]
            )
            print(result)
        finally:
            Path(filename).unlink(missing_ok=True)

    if action == "initialize":
        execute(
            "backend.app.deployment.provision",
            ["--apply"],
            {
                "RDS_BOOTSTRAP_DATABASE_URL": values["operations/master-url"],
                "RDS_BOOTSTRAP_USER": "eventquest_bootstrap",
                "DATABASE_ROLE_PASSWORDS": values["operations/role-passwords"],
            },
        )
    for purpose in ("main", "fault"):
        args = ["--purpose", purpose, "--environment", "ikonly-demo"]
        extra = {"MIGRATION_DATABASE_URL": values["operations/" + purpose + "-owner-url"]}
        if action == "initialize":
            execute("backend.app.deployment.migrate", [*args, "--apply"], extra)
            execute(
                "backend.app.deployment.bootstrap",
                [
                    *args,
                    "--policy-file",
                    "/tmp/approved-policy.json",
                    "--approved-policy-sha256",
                    config["policy_hash"],
                ],
                extra,
            )
        execute(
            "backend.app.deployment.maintenance",
            ["enable" if action == "initialize" else action, *args, "--release-id", release_id],
            extra,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action")
    parser.add_argument("--image", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    try:
        operation(args.image, args.endpoint, args.release_id, args.action)
    except Exception:  # noqa: BLE001 - suppress all DB/SDK credential-bearing diagnostics.
        raise SystemExit("Database operation failed; maintenance/profile must be checked") from None


if __name__ == "__main__":
    main()
