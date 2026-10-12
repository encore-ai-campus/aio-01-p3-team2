"""First deployment, selected/full update, recovery and complete application reset."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from infra.aws.cleanup import reset, verify_reset
from infra.aws.fleet_release import accept, database
from infra.aws.host_transport import command
from infra.aws.infrastructure import Aws, preflight, validate_config
from infra.aws.operation_state import LOCK, commit_state, operation_lock, read_state, write


def update(aws, config, state, args):
    from infra.aws.deployment_impact import plan_from_git
    from infra.aws.fleet_release import deploy
    from infra.aws.host_release import run
    from infra.aws.publish_release import publish, verify
    from infra.aws.release_plan import build

    retry = args.operation == "retry" and state and state.get("lifecycle") == "RECOVERY_REQUIRED"
    if not state or (state.get("lifecycle") != "ACTIVE" and not retry):
        write(args.output / "result.json", {"status": "INACTIVE", "created_resources": False})
        return
    if not args.directory or not args.run_id:
        raise ValueError("Update requires the exact CI artifact directory and run ID")
    metadata = json.loads(
        run(["gh", "api", f"repos/ik-e/multi-agent-autohealing/actions/runs/{args.run_id}"])
    )
    jobs = json.loads(
        run(
            [
                "gh",
                "api",
                f"repos/ik-e/multi-agent-autohealing/actions/runs/{args.run_id}/attempts/{metadata['run_attempt']}/jobs",
                "--paginate",
                "--slurp",
            ]
        )
    )
    candidate = verify(
        args.directory,
        metadata,
        [j for page in jobs for j in page["jobs"]],
        allow_current_run=args.allow_current_run,
    )
    impact = {
        role: plan_from_git(service["source_sha"], candidate["source_sha"], baseline="deployed")
        for role, service in state["release"]["services"].items()
    }
    impact_hash = hashlib.sha256(json.dumps(impact, sort_keys=True).encode()).hexdigest()
    write(args.output / "impact-review.json", {"sha256": impact_hash, "evidence": impact})
    if (
        any(x["manual_review_required"] for x in impact.values())
        and args.approve_impact_sha256 != impact_hash
    ):
        write(
            args.output / "result.json", {"status": "REVIEW_REQUIRED", "impact_sha256": impact_hash}
        )
        print("Update needs impact review: " + impact_hash)
        return
    publish(
        config,
        args.directory,
        args.run_id,
        args.output / "candidate-release.json",
        allow_current_run=args.allow_current_run,
    )
    plan = build(
        json.loads((args.output / "candidate-release.json").read_text()),
        state["release"],
        full=args.operation == "full",
    )
    if retry:
        from infra.aws.contracts import ReleaseManifest

        failure = state["failure"]
        if candidate["source_sha"] != failure["source_sha"]:
            raise ValueError(
                "Retry needs the exact failed source; changed migration code requires reconciliation"
            )
        role, source = failure["targets"][0], failure["release_id"]
        result = command(
            aws,
            config,
            state["roles"][role],
            "python3 -c \"from pathlib import Path;print(Path('/var/lib/eventquest/releases/"
            + source
            + "/release.json').read_text())\"",
            args.output / "retry-manifest-command.json",
        )
        payload = aws(
            "ssm",
            "get-command-invocation",
            "--command-id",
            result["command_id"],
            "--instance-id",
            state["roles"][role],
        )
        staged = ReleaseManifest.model_validate_json(payload["StandardOutputContent"]).model_dump()
        if (
            staged["release_id"] != source
            or staged["source_sha"] != candidate["source_sha"]
            or staged["previous_release_id"]
            != (state["release"].get("release_id") or state["release"]["source_sha"])
        ):
            raise ValueError("Failed staged manifest no longer matches the accepted baseline")
        published = json.loads((args.output / "candidate-release.json").read_text())
        for target in failure["targets"]:
            if staged["services"][target]["digest"] != published["services"][target]["digest"]:
                raise ValueError("Retry image differs from the failed staged release")
        plan.update(
            release=staged,
            targets=failure["targets"],
            migration_required=failure["migration_started"],
        )
    write(args.output / "release-plan.json", plan)
    try:
        accepted = deploy(aws, config, state, plan, args.output / "fleet")
    except Exception:
        record_path = args.output / "fleet/execution.json"
        result = (
            json.loads(record_path.read_text())
            if record_path.exists()
            else {"status": "STAGING_FAILED"}
        )
        current = read_state(aws)
        if (
            current
            and (current.get("release") or {}).get("release_id") == plan["release"]["release_id"]
        ):
            raise RuntimeError(
                "Release accepted but local report write failed; verify persisted accepted state"
            ) from None
        if result["status"] not in ("ROLLED_BACK", "STAGING_FAILED", "STAGING"):
            state = {
                **state,
                "lifecycle": "RECOVERY_REQUIRED",
                "failure": {
                    "release_id": plan["release"]["release_id"],
                    "targets": plan["targets"],
                    "migration_started": result.get("migration_started", False),
                    "source_sha": plan["release"]["source_sha"],
                    "migration_backup": result.get("migration_backup"),
                },
            }
            commit_state(aws, state)
        write(args.output / "result.json", result)
        raise
    write(
        args.output / "result.json",
        {
            "status": "ACCEPTED" if plan["targets"] else "NO_OP",
            "release_id": accepted["release"].get("release_id"),
            "targets": plan["targets"],
        },
    )


def recover(aws, config, state, output):
    if not state or not state.get("release") or not state.get("failure"):
        raise ValueError("Recovery needs a recorded failed update and accepted previous release")
    if state["failure"]["migration_started"]:
        raise ValueError(
            "DB/catalog migration started; inspect backup and reconcile before recovery"
        )
    previous = state["release"]
    for role in reversed(state["failure"]["targets"]):
        source = (
            previous["services"][role].get("deployment_id")
            or previous["services"][role]["source_sha"]
        )
        script = (
            "set -eu\npython3 - <<'PY'\nimport hashlib,subprocess\nfrom pathlib import Path\nsource="
            + repr(source)
            + "\nraw=Path('/var/lib/eventquest/releases/'+source+'/release.json').read_bytes()\nsubprocess.run(['/opt/eventquest/bin/deploy-release',source,hashlib.sha256(raw).hexdigest()],check=True)\nPY"
        )
        command(aws, config, state["roles"][role], script, output / (role + "-recover.json"))
    accept(aws, config, state, previous, output / "acceptance")
    if any(not role.endswith("web") for role in state["failure"]["targets"]):
        database(
            aws,
            config,
            state,
            {**previous, "release_id": state["failure"]["release_id"]},
            "disable",
            output,
        )
    restored = {k: v for k, v in state.items() if k != "failure"}
    restored["lifecycle"] = "ACTIVE"
    commit_state(aws, restored)
    write(output / "result.json", {"status": "RECOVERED", "accepted_release_unchanged": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "status",
            "oidc-check",
            "initial",
            "update",
            "full",
            "retry",
            "recover",
            "reset",
            "unlock",
        ),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--approved-policy-sha256")
    parser.add_argument("--approve-impact-sha256")
    parser.add_argument("--allow-current-run", action="store_true")
    parser.add_argument("--confirm-environment")
    parser.add_argument("--lock-token")
    args = parser.parse_args()
    if args.operation == "reset" and os.getenv("GITHUB_ACTIONS") == "true":
        raise ValueError("Reset requires the operator's local persistent evidence directory")
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    aws = Aws(config["region"])
    args.output.mkdir(parents=True, exist_ok=True)
    identity = preflight(aws, config)
    write(args.output / "identity.json", identity)
    if args.operation == "unlock":
        if args.confirm_environment != "ikonly-demo" or not args.lock_token:
            raise ValueError("Unlock needs explicit environment and exact recorded lock token")
        values = aws(
            "ssm", "get-parameters", payload={"Names": [LOCK], "WithDecryption": False}
        ).get("Parameters", [])
        if len(values) != 1:
            raise ValueError("No unique operation lock exists")
        owner = json.loads(values[0]["Value"])
        if owner["token"] != args.lock_token:
            raise ValueError("Lock token differs; another operation owns it")
        if owner.get("github_run_id"):
            from infra.aws.host_release import run

            workflow = json.loads(
                run(
                    [
                        "gh",
                        "api",
                        "repos/ik-e/multi-agent-autohealing/actions/runs/" + owner["github_run_id"],
                    ]
                )
            )
            if workflow["status"] != "completed":
                raise ValueError("Lock owner workflow is still active")
        aws("ssm", "delete-parameter", "--name", LOCK)
        write(args.output / "result.json", {"status": "LOCK_CLEARED", "owner": owner})
        return
    if args.operation in ("status", "oidc-check"):
        if (
            args.operation == "oidc-check"
            and ":assumed-role/ikonly-control-controller/" not in identity["principal_arn"]
        ):
            raise ValueError("OIDC check must run with the controller role session")
        write(
            args.output / "result.json",
            {
                "status": "OIDC_CONNECTED" if args.operation == "oidc-check" else "STATUS",
                "lifecycle": (read_state(aws) or {}).get("lifecycle", "ABSENT"),
                "remaining": verify_reset(aws),
            },
        )
        return
    # Automatic updates must not recreate a reset environment or hold the lock
    # while the local first deployment is still waiting for this CI run to finish.
    if (
        args.operation in ("update", "full")
        and (read_state(aws) or {}).get("lifecycle") != "ACTIVE"
    ):
        write(args.output / "result.json", {"status": "INACTIVE", "created_resources": False})
        return
    with operation_lock(aws, args.operation, args.output):
        state = read_state(aws)
        if state:
            config = validate_config(state["config"])
            preflight(aws, config)
        if args.operation == "initial":
            if state or not all(
                (
                    args.directory,
                    args.run_id,
                    args.env_file,
                    args.policy_file,
                    args.approved_policy_sha256,
                )
            ):
                raise ValueError(
                    "Initial deployment needs an empty state, CI artifact, local env and approved policy"
                )
            from infra.aws.initial_deployment import initial

            try:
                accepted = initial(
                    aws,
                    config,
                    args.directory,
                    args.run_id,
                    args.env_file,
                    args.policy_file,
                    args.approved_policy_sha256,
                    args.output,
                )
                write(
                    args.output / "result.json",
                    {
                        "status": "INITIAL_ACCEPTED",
                        "release_id": accepted["release"]["release_id"],
                        "source_sha": accepted["release"]["source_sha"],
                    },
                )
            except Exception:
                marker = args.output / "initial-start.json"
                token = json.loads((args.output / "lock.json").read_text())["token"]
                current = read_state(aws)
                if (current or {}).get("lifecycle") == "ACTIVE":
                    write(args.output / "result.json", {"status": "ACCEPTED_LOCAL_REPORT_ERROR"})
                elif marker.exists() and json.loads(marker.read_text()).get("lock_token") == token:
                    reset(aws, config, read_state(aws), args.output)
                    write(
                        args.output / "result.json",
                        {"status": "INITIAL_FAILED_AND_RESET", "evidence_saved_locally": True},
                    )
                raise
        elif args.operation in ("update", "full", "retry"):
            update(aws, config, state, args)
        elif args.operation == "recover":
            recover(aws, config, state, args.output)
        else:
            if args.confirm_environment != "ikonly-demo":
                raise ValueError("Reset needs explicit --confirm-environment ikonly-demo")
            if state and state.get("release"):
                release = state["release"]
                if state.get("failure"):
                    release = {**release, "release_id": state["failure"]["release_id"]}
                database(aws, config, state, release, "enable", args.output / "quiesce")
                database(aws, config, state, release, "drain", args.output / "quiesce")
            reset(aws, config, state, args.output)
            write(
                args.output / "result.json",
                {
                    "status": "RESET_COMPLETE",
                    "evidence_saved_locally": True,
                    "control_identity_removed": False,
                },
            )
    print("Operation complete; inspect result metadata in " + str(args.output))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - hide cloud/SDK/third-party credential-bearing details.
        raise SystemExit(
            "AWS operation failed; inspect recorded metadata and resume safely"
        ) from None
