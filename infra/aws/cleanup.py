"""Reset only owned application resources after downloading evidence locally."""

import base64
import hashlib
import json
import time
from pathlib import Path

from infra.aws.host_transport import command
from infra.aws.iac import PREFIX
from infra.aws.infrastructure import owned_stack
from infra.aws.operation_state import write


def pages(aws, args, key, *, token_key="NextToken", option="--next-token"):
    token = None
    while True:
        response = aws(*args, *([option, token] if token else []))
        yield from response.get(key, [])
        new = response.get(token_key)
        if not new:
            return
        if new == token:
            raise RuntimeError("Pagination did not advance")
        token = new


def check_tags(tags):
    if isinstance(tags, list):
        tags = {x.get("Key", x.get("key")): x.get("Value", x.get("value")) for x in tags}
    if tags.get("Environment") != "ikonly-demo" or tags.get("ManagedBy") != "ikonly-iac":
        raise ValueError("Reset refuses resources without matching ownership tags")


def journal(aws, config, state, output):
    if not state or (state.get("lifecycle") == "INITIALIZING" and not state.get("release")):
        return
    instance = state["roles"]["fault-api"]
    response = aws("ec2", "describe-instances", "--instance-ids", instance)
    hosts = [i for r in response["Reservations"] for i in r["Instances"]]
    if not hosts or hosts[0]["State"]["Name"] != "running":
        return
    script = "set -eu\npython3 - <<'PY'\nimport io,tarfile,gzip,hashlib,json\nfrom pathlib import Path\ns=io.BytesIO()\nwith tarfile.open(fileobj=s,mode='w') as tar:\n for p in Path('/var/lib/eventquest/fault-lab').glob('*'):\n  if p.is_file() and p.suffix in ('.json','.jsonl'): tar.add(p,arcname=p.name)\nraw=gzip.compress(s.getvalue());Path('/tmp/eventquest-reset-journal.gz').write_bytes(raw)\nprint(json.dumps({'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}))\nPY"
    result = command(aws, config, instance, script, output / "journal-create.json")
    details = aws(
        "ssm",
        "get-command-invocation",
        "--command-id",
        result["command_id"],
        "--instance-id",
        instance,
    )
    metadata = json.loads(details["StandardOutputContent"].strip())
    content = bytearray()
    for offset in range(0, metadata["bytes"], 6000):
        chunk = command(
            aws,
            config,
            instance,
            "python3 -c \"import base64;f=open('/tmp/eventquest-reset-journal.gz','rb');f.seek("
            + str(offset)
            + ');print(base64.b64encode(f.read(6000)).decode())"',
            output / f"journal-chunk-{offset}.json",
        )
        value = aws(
            "ssm",
            "get-command-invocation",
            "--command-id",
            chunk["command_id"],
            "--instance-id",
            instance,
        )
        content.extend(base64.b64decode(value["StandardOutputContent"].strip(), validate=True))
    if (
        len(content) != metadata["bytes"]
        or hashlib.sha256(content).hexdigest() != metadata["sha256"]
    ):
        raise ValueError("Fault journal download hash mismatch")
    (output / "fault-journal.tar.gz").write_bytes(content)
    write(output / "journal-metadata.json", metadata)


def archive_bucket(aws, bucket, output):
    output.mkdir(parents=True, exist_ok=True)
    entries, markers = [], []
    request = {"Bucket": bucket}
    while True:
        response = aws("s3api", "list-object-versions", payload=request)
        entries.extend(response.get("Versions", []))
        markers.extend(response.get("DeleteMarkers", []))
        if not response.get("IsTruncated"):
            break
        request.update(
            KeyMarker=response["NextKeyMarker"], VersionIdMarker=response["NextVersionIdMarker"]
        )
    index = []
    for n, obj in enumerate(entries):
        target = output / f"{n:06d}.bin"
        args = (
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            obj["Key"],
            "--version-id",
            obj["VersionId"],
        )
        aws(*args, str(target))
        second = output / f"{n:06d}.verify"
        aws(*args, str(second))
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if (
            target.stat().st_size != obj["Size"]
            or hashlib.sha256(second.read_bytes()).hexdigest() != sha
        ):
            raise ValueError("Evidence object download integrity mismatch")
        index.append(
            {
                "key": obj["Key"],
                "version_id": obj["VersionId"],
                "file": target.name,
                "bytes": obj["Size"],
                "sha256": sha,
            }
        )
    write(output / "index.json", index)
    return entries + markers


def reset(aws, config, state, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stacks = list(pages(aws, ("cloudformation", "describe-stacks"), "Stacks"))
    names = {
        "ikonly-demo-" + stage
        for stage in ("network", "access", "data", "application", "observability")
    }
    selected = {s["StackName"]: s for s in stacks if s["StackName"] in names}
    for stack in selected.values():
        if stack["StackStatus"] == "REVIEW_IN_PROGRESS" and not stack.get("Tags"):
            stage = stack["StackName"].removeprefix("ikonly-demo-")
            source = output / (stage + "-plan.json")
            if not source.exists():
                raise ValueError("Untagged initial placeholder requires its local creation record")
            record = json.loads(source.read_text())
            if record["stack_id"] != stack["StackId"]:
                raise ValueError("Placeholder stack identity differs from local creation record")
            planned = aws(
                "cloudformation",
                "describe-change-set",
                "--change-set-name",
                record["change_set_id"],
            )
            owned_stack(planned)
            if planned["StackId"] != stack["StackId"] or any(
                x["ResourceChange"]["Action"] != "Add" for x in planned.get("Changes", [])
            ):
                raise ValueError("Placeholder change set is not a new owned stack")
        else:
            owned_stack(stack)
    write(output / "stack-inventory.json", selected)
    journal(aws, config, state, output)
    buckets = []
    for item in aws("s3api", "list-buckets")["Buckets"]:
        name = item["Name"]
        if name.startswith("ikonly-demo-observability-evidence-"):
            check_tags(aws("s3api", "get-bucket-tagging", "--bucket", name)["TagSet"])
            versions = archive_bucket(aws, name, output / name)
            buckets.append((name, versions))
    log_groups = []
    for prefix in ("/ikonly-demo/", "/aws/rds/instance/ikonly-demo-postgres/"):
        for item in pages(
            aws,
            ("logs", "describe-log-groups", "--log-group-name-prefix", prefix),
            "logGroups",
            token_key="nextToken",
        ):
            name = item["logGroupName"]
            if prefix == "/ikonly-demo/":
                check_tags(aws("logs", "list-tags-log-group", "--log-group-name", name)["tags"])
            # RDS creates these two exact log groups outside the stack/tag index.
            elif name not in (prefix + "postgresql", prefix + "upgrade"):
                raise ValueError("Unexpected RDS automatic log group")
            events = list(
                pages(
                    aws,
                    ("logs", "filter-log-events", "--log-group-name", name),
                    "events",
                    token_key="nextToken",
                )
            )
            write(
                output / (hashlib.sha256(name.encode()).hexdigest() + "-logs.json"),
                {"name": name, "events": events},
            )
            log_groups.append(name)
    write(
        output / "execution.json",
        {"status": "EVIDENCE_DOWNLOADED", "stacks": list(selected), "log_groups": log_groups},
    )
    for stage in ("observability", "application", "data", "access", "network"):
        stack = selected.get("ikonly-demo-" + stage)
        if stack is None:
            continue
        if stage == "data":
            databases = aws("rds", "describe-db-instances")["DBInstances"]
            for db in databases:
                if db["DBInstanceIdentifier"] == "ikonly-demo-postgres":
                    check_tags(
                        aws(
                            "rds", "list-tags-for-resource", "--resource-name", db["DBInstanceArn"]
                        )["TagList"]
                    )
                    aws(
                        "rds",
                        "modify-db-instance",
                        payload={
                            "DBInstanceIdentifier": db["DBInstanceIdentifier"],
                            "DeletionProtection": False,
                            "ApplyImmediately": True,
                        },
                    )
                    deadline = time.monotonic() + 600
                    while time.monotonic() < deadline:
                        current = aws(
                            "rds",
                            "describe-db-instances",
                            "--db-instance-identifier",
                            db["DBInstanceIdentifier"],
                        )["DBInstances"][0]
                        if not current["DeletionProtection"]:
                            break
                        time.sleep(5)
                    else:
                        raise TimeoutError("RDS protection change timeout")
        aws("cloudformation", "delete-stack", "--stack-name", stack["StackId"])
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            value = aws("cloudformation", "describe-stacks", "--stack-name", stack["StackId"])[
                "Stacks"
            ][0]
            if value["StackStatus"] == "DELETE_COMPLETE":
                break
            if value["StackStatus"] == "DELETE_FAILED":
                write(
                    output / (stage + "-failure.json"),
                    aws(
                        "cloudformation", "describe-stack-events", "--stack-name", stack["StackId"]
                    ),
                )
                raise RuntimeError(
                    "Stack deletion failed; resume reset after resolving owned blockers"
                )
            time.sleep(10)
        else:
            raise TimeoutError("Stack deletion timed out")
    # Purge after stack deletion so retained/CFN final snapshots are also included.
    for snap in aws(
        "rds", "describe-db-snapshots", "--db-instance-identifier", "ikonly-demo-postgres"
    )["DBSnapshots"]:
        if snap["SnapshotType"] == "manual" and snap["Status"] != "deleting":
            check_tags(
                aws("rds", "list-tags-for-resource", "--resource-name", snap["DBSnapshotArn"])[
                    "TagList"
                ]
            )
            aws(
                "rds",
                "delete-db-snapshot",
                "--db-snapshot-identifier",
                snap["DBSnapshotIdentifier"],
            )
    for repo in pages(aws, ("ecr", "describe-repositories"), "repositories", token_key="nextToken"):
        if repo["repositoryName"] in ("ikonly-demo/application", "ikonly-demo/web"):
            check_tags(
                aws("ecr", "list-tags-for-resource", "--resource-arn", repo["repositoryArn"])[
                    "tags"
                ]
            )
            aws("ecr", "delete-repository", "--repository-name", repo["repositoryName"], "--force")
    # Include shutdown events and log groups created while RDS was deleting.
    for prefix in ("/ikonly-demo/", "/aws/rds/instance/ikonly-demo-postgres/"):
        for group in pages(
            aws,
            ("logs", "describe-log-groups", "--log-group-name-prefix", prefix),
            "logGroups",
            token_key="nextToken",
        ):
            name = group["logGroupName"]
            if prefix == "/ikonly-demo/":
                check_tags(aws("logs", "list-tags-log-group", "--log-group-name", name)["tags"])
            elif name not in (prefix + "postgresql", prefix + "upgrade"):
                raise ValueError("Unexpected automatic DB log group")
            events = list(
                pages(
                    aws,
                    ("logs", "filter-log-events", "--log-group-name", name),
                    "events",
                    token_key="nextToken",
                )
            )
            write(
                output / (hashlib.sha256(name.encode()).hexdigest() + "-logs-final.json"),
                {"name": name, "events": events},
            )
            if name not in log_groups:
                log_groups.append(name)
    for name in log_groups:
        aws("logs", "delete-log-group", "--log-group-name", name)
    for name, versions in buckets:
        versions = archive_bucket(aws, name, output / name / "final")
        for obj in versions:
            aws(
                "s3api",
                "delete-object",
                "--bucket",
                name,
                "--key",
                obj["Key"],
                "--version-id",
                obj["VersionId"],
            )
        aws("s3api", "delete-bucket", "--bucket", name)
    names = list(
        pages(
            aws,
            (
                "ssm",
                "describe-parameters",
                "--parameter-filters",
                "Key=Name,Option=BeginsWith,Values=" + PREFIX,
            ),
            "Parameters",
        )
    )
    for item in names:
        check_tags(
            aws(
                "ssm",
                "list-tags-for-resource",
                "--resource-type",
                "Parameter",
                "--resource-id",
                item["Name"],
            )["TagList"]
        )
        aws("ssm", "delete-parameter", "--name", item["Name"])
    deadline = time.monotonic() + 600
    while True:
        remaining = verify_reset(aws)
        write(output / "residual-check.json", remaining)
        if not any(remaining.values()):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Reset still has remaining owned resources")
        time.sleep(10)
    write(
        output / "execution.json",
        {
            "status": "RESET_COMPLETE",
            "evidence_location": str(output),
            "control_identity_removed": False,
        },
    )


def verify_reset(aws):
    env = "Name=tag:Environment,Values=ikonly-demo"
    instances = aws("ec2", "describe-instances", "--filters", env)["Reservations"]
    nat = aws("ec2", "describe-nat-gateways", "--filter", env)["NatGateways"]
    return {
        "runtime_roles": [
            x["RoleName"]
            for x in pages(
                aws, ("iam", "list-roles"), "Roles", token_key="Marker", option="--marker"
            )
            if x["RoleName"].startswith("ikonly-demo-")
        ],
        "runtime_profiles": [
            x["InstanceProfileName"]
            for x in pages(
                aws,
                ("iam", "list-instance-profiles"),
                "InstanceProfiles",
                token_key="Marker",
                option="--marker",
            )
            if x["InstanceProfileName"].startswith("ikonly-demo-access-")
        ],
        "deploy_documents": [
            x["Name"]
            for x in pages(
                aws,
                ("ssm", "list-documents", "--filters", "Key=Owner,Values=Self"),
                "DocumentIdentifiers",
            )
            if x["Name"] == "IkonlyDeploy"
        ],
        "alarms": [
            x["AlarmName"]
            for x in pages(aws, ("cloudwatch", "describe-alarms"), "MetricAlarms")
            if x["AlarmName"].startswith("ikonly-demo-")
        ],
        "topics": [
            x["TopicArn"]
            for x in pages(aws, ("sns", "list-topics"), "Topics")
            if x["TopicArn"].split(":")[-1].startswith("ikonly-demo-")
        ],
        "active_instances": [
            i["InstanceId"]
            for r in instances
            for i in r["Instances"]
            if i["State"]["Name"] != "terminated"
        ],
        "active_nat": [x["NatGatewayId"] for x in nat if x["State"] != "deleted"],
        "vpcs": [x["VpcId"] for x in aws("ec2", "describe-vpcs", "--filters", env)["Vpcs"]],
        "eips": [
            x["AllocationId"]
            for x in aws("ec2", "describe-addresses", "--filters", env)["Addresses"]
        ],
        "volumes": [
            x["VolumeId"] for x in aws("ec2", "describe-volumes", "--filters", env)["Volumes"]
        ],
        "databases": [
            x["DBInstanceIdentifier"]
            for x in aws("rds", "describe-db-instances")["DBInstances"]
            if x["DBInstanceIdentifier"] == "ikonly-demo-postgres"
        ],
        "snapshots": [
            x["DBSnapshotIdentifier"]
            for x in aws(
                "rds", "describe-db-snapshots", "--db-instance-identifier", "ikonly-demo-postgres"
            )["DBSnapshots"]
        ],
        "automated_backups": [
            x["DBInstanceAutomatedBackupsArn"]
            for x in aws("rds", "describe-db-instance-automated-backups")[
                "DBInstanceAutomatedBackups"
            ]
            if x["DBInstanceIdentifier"] == "ikonly-demo-postgres"
        ],
        "parameters": [
            x["Name"]
            for x in pages(
                aws,
                (
                    "ssm",
                    "describe-parameters",
                    "--parameter-filters",
                    "Key=Name,Option=BeginsWith,Values=" + PREFIX,
                ),
                "Parameters",
            )
        ],
        "repositories": [
            x["repositoryName"]
            for x in pages(
                aws, ("ecr", "describe-repositories"), "repositories", token_key="nextToken"
            )
            if x["repositoryName"].startswith("ikonly-demo/")
        ],
        "logs": [
            x["logGroupName"]
            for prefix in ("/ikonly-demo/", "/aws/rds/instance/ikonly-demo-postgres/")
            for x in pages(
                aws,
                ("logs", "describe-log-groups", "--log-group-name-prefix", prefix),
                "logGroups",
                token_key="nextToken",
            )
        ],
        "buckets": [
            x["Name"]
            for x in aws("s3api", "list-buckets")["Buckets"]
            if x["Name"].startswith("ikonly-demo-observability-evidence-")
        ],
    }
