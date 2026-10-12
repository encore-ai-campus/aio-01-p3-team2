"""Stage selected roles, accept dependencies, and recover an unchanged DB release."""

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

from infra.aws.host_transport import command, db_script, stage_script, support_script
from infra.aws.infrastructure import owned_stack
from infra.aws.operation_state import commit_state, write


@contextmanager
def db_profile(aws, instance, profile):
    def association():
        values = aws(
            "ec2",
            "describe-iam-instance-profile-associations",
            payload={"Filters": [{"Name": "instance-id", "Values": [instance]}]},
        )["IamInstanceProfileAssociations"]
        active = [x for x in values if x["State"] == "associated"]
        if len(active) != 1:
            raise ValueError("Host instance profile association is ambiguous")
        return active[0]

    original = association()["IamInstanceProfile"]["Arn"].split("/")[-1]
    aws(
        "ec2",
        "replace-iam-instance-profile-association",
        payload={
            "AssociationId": association()["AssociationId"],
            "IamInstanceProfile": {"Name": profile},
        },
    )
    try:
        time.sleep(15)
        yield
    finally:
        # Re-read the current association ID instead of reusing the pre-replace ID.
        aws(
            "ec2",
            "replace-iam-instance-profile-association",
            payload={
                "AssociationId": association()["AssociationId"],
                "IamInstanceProfile": {"Name": original},
            },
        )
        time.sleep(15)


def database(aws, config, state, release, action, output):
    stack = aws("cloudformation", "describe-stacks", "--stack-name", "ikonly-demo-access")[
        "Stacks"
    ][0]
    owned_stack(stack)
    profiles = {x["OutputKey"]: x["OutputValue"] for x in stack["Outputs"]}
    profile = profiles["BootstrapProfile" if action == "initialize" else "MigrationProfile"]
    with db_profile(aws, state["roles"]["main-api"], profile):
        # EC2 credential propagation can lag association completion. Each action is
        # idempotent; all command results are recorded before bounded retry.
        for attempt in range(3):
            try:
                return command(
                    aws,
                    config,
                    state["roles"]["main-api"],
                    db_script(release, config["account_id"], state["endpoint"], action),
                    Path(output) / f"database-{action}-{attempt}.json",
                )
            except RuntimeError:
                if attempt == 2:
                    raise
                time.sleep(20)


def acceptance_script(role, service):
    # Retrieve runtime values within the host only; never return inspect Env/headers.
    checks = """
import http.client,json,socket,ssl,subprocess,urllib.request
from pathlib import Path
container=subprocess.check_output(['docker','ps','--filter','label=com.docker.compose.project=eventquest','--format','{{.ID}}'],text=True).strip().splitlines()
assert len(container)==1
item=json.loads(subprocess.check_output(['docker','inspect',container[0]],text=True))[0]
assert item['State']['Health']['Status']=='healthy'
assert item['Config']['Labels']['org.opencontainers.image.revision']==expected['source_sha']
assert item['Config']['Image'].endswith('@'+expected['digest'])
env=dict(x.split('=',1) for x in item['Config']['Env'])
if role.endswith('web'):
 ctx=ssl.create_default_context(cafile='/var/lib/eventquest/caddy-data/caddy/pki/authorities/local/root.crt')
 conn=http.client.HTTPSConnection(env['PUBLIC_HOST'],timeout=15,context=ctx)
 conn.sock=ctx.wrap_socket(socket.create_connection(('127.0.0.1',443),timeout=15),server_hostname=env['PUBLIC_HOST'])
 conn.request('GET','/');response=conn.getresponse();assert response.status==401;response.read();conn.close()
 peers=[(env['MAIN_API_UPSTREAM']+'/api/game/state',env['MAIN_PROXY_KEY'])]
 if role=='plan-web': peers.append((env['FAULT_API_UPSTREAM']+'/api/test/fault-catalog',env['FAULT_PROXY_KEY']))
 for url,key in peers:
  req=urllib.request.Request(url,headers={'X-Eventquest-Proxy':role,'X-Eventquest-Proxy-Key':key})
  with urllib.request.urlopen(req,timeout=20) as r: assert r.status==200;json.load(r)
else:
 url='http://127.0.0.1:'+('8010' if role.endswith('mcp') else '8000')+'/health/ready'
 with urllib.request.urlopen(url,timeout=20) as r: assert r.status==200 and json.load(r)['status']=='ready'
print('Image identity, health and dependency acceptance passed')
"""
    return (
        "set -eu\npython3 - <<'PY'\nrole="
        + repr(role)
        + "\nexpected="
        + repr(service)
        + "\n"
        + checks
        + "\nPY"
    )


def accept(aws, config, state, release, output):
    for role, instance in state["roles"].items():
        command(
            aws,
            config,
            instance,
            acceptance_script(role, release["services"][role]),
            Path(output) / (role + "-acceptance.json"),
        )


def migration_backup(aws, release, output):
    name = "ikonly-demo-before-" + release["release_id"][:24]
    values = aws(
        "rds", "describe-db-snapshots", "--db-instance-identifier", "ikonly-demo-postgres"
    )["DBSnapshots"]
    if not any(x["DBSnapshotIdentifier"] == name for x in values):
        aws(
            "rds",
            "create-db-snapshot",
            payload={
                "DBSnapshotIdentifier": name,
                "DBInstanceIdentifier": "ikonly-demo-postgres",
                "Tags": [
                    {"Key": "Environment", "Value": "ikonly-demo"},
                    {"Key": "ManagedBy", "Value": "ikonly-iac"},
                ],
            },
        )
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        snapshot = aws("rds", "describe-db-snapshots", "--db-snapshot-identifier", name)[
            "DBSnapshots"
        ][0]
        if snapshot["DBInstanceIdentifier"] != "ikonly-demo-postgres" or not snapshot["Encrypted"]:
            raise ValueError("Migration backup identity or encryption mismatch")
        tags = {
            tag["Key"]: tag["Value"]
            for tag in aws(
                "rds", "list-tags-for-resource", "--resource-name", snapshot["DBSnapshotArn"]
            )["TagList"]
        }
        if tags.get("Environment") != "ikonly-demo" or tags.get("ManagedBy") != "ikonly-iac":
            raise ValueError("Migration backup ownership mismatch")
        write(
            Path(output) / "migration-backup.json",
            {"name": name, "status": snapshot["Status"], "encrypted": True},
        )
        if snapshot["Status"] == "available":
            return name
        if snapshot["Status"] != "creating":
            raise RuntimeError("Migration backup failed")
        time.sleep(10)
    raise TimeoutError("Migration backup availability deadline")


def deploy(aws, config, state, plan, output, *, configs=None, policy=None, database_config=None):
    output = Path(output)
    release, targets = plan["release"], plan["targets"]
    if not targets:
        write(output / "result.json", {"status": "NO_OP", "accepted_release": state.get("release")})
        return state
    previous = state.get("release")
    record = {
        "status": "STAGING",
        "targets": targets,
        "attempted": [],
        "maintenance": False,
        "migration_started": bool((state.get("failure") or {}).get("migration_started")),
        "admission_released": False,
    }
    write(output / "execution.json", record)
    raw = json.dumps(release, indent=2).encode()
    source = release.get("release_id") or release["source_sha"]
    sha = hashlib.sha256(raw).hexdigest()
    app_stack = aws("cloudformation", "describe-stacks", "--stack-name", "ikonly-demo-application")[
        "Stacks"
    ][0]
    owned_stack(app_stack)
    outputs = {x["OutputKey"]: x["OutputValue"] for x in app_stack["Outputs"]}
    from infra.aws.iac import logical

    for role in targets:
        instance = state["roles"][role]
        deployment = {
            "account_id": config["account_id"],
            "private_ip": outputs[logical(role) + "PrivateIp"],
        }
        command(
            aws,
            config,
            instance,
            support_script(source, deployment, release["source_sha"]),
            output / (role + "-support.json"),
        )
        root = "/var/lib/eventquest/releases/" + source
        files = {root + "/release.json": raw}
        if configs:
            files[root + "/host-config.json"] = json.dumps(configs[role]).encode()
        else:
            old = (
                previous["services"][role].get("deployment_id")
                or previous["services"][role]["source_sha"]
            )
            # Existing host config/CA/proxy keys survive updates without re-seeding.
            script = (
                "set -eu\npython3 - <<'PY'\nfrom pathlib import Path\np=Path("
                + repr(root + "/host-config.json")
                + ");raw=Path("
                + repr("/var/lib/eventquest/releases/" + old + "/host-config.json")
                + ").read_bytes()\nif p.exists(): assert p.read_bytes()==raw\nelse: p.write_bytes(raw);p.chmod(0o600)\nPY"
            )
            command(aws, config, instance, script, output / (role + "-config.json"))
        if role == "main-api" and policy is not None:
            files["/etc/eventquest/approved-policy.json"] = policy
            files["/etc/eventquest/database-operation.json"] = json.dumps(database_config).encode()
        command(aws, config, instance, stage_script(files), output / (role + "-stage.json"))
        if role == "main-api" and policy is not None:
            command(
                aws,
                config,
                instance,
                "chmod 0644 /etc/eventquest/approved-policy.json",
                output / "policy-permission.json",
            )
    try:
        needs_maintenance = previous is None or any(not role.endswith("web") for role in targets)
        if previous is None:
            database(aws, config, state, release, "initialize", output)
            record["maintenance"] = True
        elif needs_maintenance:
            # Use the currently deployed image to admit/drain old work before migration.
            control = {**previous, "release_id": source}
            record["maintenance"] = True
            write(output / "execution.json", record)
            database(aws, config, state, control, "enable", output)
            database(aws, config, state, control, "drain", output)
            if (
                plan["migration_required"]
                or previous["catalog_checksum"] != release["catalog_checksum"]
            ):
                record["migration_backup"] = migration_backup(aws, release, output)
                record["migration_started"] = True
                write(output / "execution.json", record)
                database(aws, config, state, release, "migrate", output)
        for role in ("main-mcp", "fault-mcp", "main-api", "fault-api", "plan-web", "game-web"):
            if role not in targets:
                continue
            record["attempted"].append(role)
            write(output / "execution.json", record)
            command(
                aws,
                config,
                state["roles"][role],
                "",
                output / (role + "-deploy.json"),
                document="IkonlyDeploy",
                parameters={"ReleaseId": [source], "ManifestSha256": [sha]},
            )
        accept(aws, config, state, release, output)
        if record["maintenance"]:
            database(aws, config, state, release, "disable", output)
            record["admission_released"] = True
        accepted = {
            **{k: v for k, v in state.items() if k != "failure"},
            "lifecycle": "ACTIVE",
            "release": release,
            "accepted_at": time.time(),
        }
        commit_state(aws, accepted)
    except Exception:  # noqa: BLE001 - every deployment failure must enter bounded recovery.
        record["status"] = "SAFE_STOP"
        write(output / "execution.json", record)
        # A migration can make older images unsafe; do not silently reverse DB state.
        if previous is not None and not record["migration_started"]:
            try:
                if record["admission_released"]:
                    database(aws, config, state, release, "enable", output / "rollback")
                    database(aws, config, state, release, "drain", output / "rollback")
                for role in reversed(record["attempted"]):
                    old = (
                        previous["services"][role].get("deployment_id")
                        or previous["services"][role]["source_sha"]
                    )
                    # Read the immutable OLD manifest on each host. It may precede
                    # the fleet baseline after a previous selective deployment.
                    script = (
                        "set -eu\npython3 - <<'PY'\nimport hashlib,subprocess\nfrom pathlib import Path\nsource="
                        + repr(old)
                        + "\nraw=Path('/var/lib/eventquest/releases/'+source+'/release.json').read_bytes()\nsubprocess.run(['/opt/eventquest/bin/deploy-release',source,hashlib.sha256(raw).hexdigest()],check=True)\nPY"
                    )
                    command(
                        aws,
                        config,
                        state["roles"][role],
                        script,
                        output / (role + "-rollback.json"),
                    )
                accept(aws, config, state, previous, output / "rollback")
                if record["maintenance"]:
                    database(
                        aws,
                        config,
                        state,
                        {**previous, "release_id": source},
                        "disable",
                        output / "rollback",
                    )
                record["status"] = "ROLLED_BACK"
            except Exception:  # noqa: BLE001 - preserve maintenance for any failed rollback.
                record["status"] = "RECOVERY_REQUIRED"
            write(output / "execution.json", record)
        raise RuntimeError(
            "Fleet update failed; inspect execution status before retrying"
        ) from None
    record["status"] = "ACCEPTED"
    write(output / "execution.json", record)
    return accepted
