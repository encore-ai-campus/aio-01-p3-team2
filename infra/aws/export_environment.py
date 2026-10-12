"""Export owned physical IDs only after every stack is complete."""

import argparse
import json
from pathlib import Path

from infra.aws.contracts import EnvironmentManifest
from infra.aws.iac import PARAMETERS, PREFIX, ROLES, generated, logical
from infra.aws.infrastructure import Aws, owned_stack, preflight, validate_config


def export(aws, config, *, partial=False):
    preflight(aws, config)
    resources, outputs, statuses = [], {}, {}
    known_stacks = {
        item["StackName"]: item
        for item in aws("cloudformation", "describe-stacks").get("Stacks", [])
    }
    for stage in ("network", "observability", "access", "data", "application"):
        name = "ikonly-demo-" + stage
        stack = known_stacks.get(name)
        if stack is None:
            if partial:
                statuses[name] = "NOT_CREATED"
                continue
            raise ValueError("Required stack is missing")
        owned_stack(stack)
        statuses[name] = stack["StackStatus"]
        if not partial and stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise ValueError("Stack must be complete before environment export")
        if stage == "application":
            outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
        declared = generated()["stacks/" + stage + ".yml"]["Resources"]
        token = None
        while True:
            args = ["cloudformation", "list-stack-resources", "--stack-name", name]
            if token:
                args += ["--next-token", token]
            response = aws(*args)
            for item in response["StackResourceSummaries"]:
                if not partial and item["ResourceStatus"] not in (
                    "CREATE_COMPLETE",
                    "UPDATE_COMPLETE",
                ):
                    raise ValueError("Incomplete physical resource")
                identifier = item.get("PhysicalResourceId")
                if not identifier:
                    if partial:
                        continue
                    raise ValueError("Physical ID missing")
                resources.append(
                    {
                        "id": identifier,
                        "kind": item["ResourceType"],
                        "owner_stack": name,
                        "environment_id": "ikonly-demo",
                        # Snapshot protects the backup, not the running RDS instance.
                        "retain": declared[item["LogicalResourceId"]].get("DeletionPolicy")
                        == "Retain",
                    }
                )
            token = response.get("NextToken")
            if not token:
                break
    # Physical IDs are unique within resource types, not across types (S3 Bucket/Policy).
    if len({(item["kind"], item["id"]) for item in resources}) != len(resources):
        raise ValueError("Physical ID collision requires manifest contract review")
    if partial:
        return {
            "status": "PARTIAL_RESOURCE_SNAPSHOT",
            "account_id": config["account_id"],
            "environment_id": config["environment_id"],
            "region": config["region"],
            "stacks": statuses,
            "resources": resources,
            "deployment_authorized": False,
        }
    manifest = EnvironmentManifest.model_validate(
        {
            "version": 1,
            "account_id": config["account_id"],
            "region": config["region"],
            "environment_id": config["environment_id"],
            "roles": {role: outputs[logical(role)] for role in ROLES},
            "resources": resources,
            "parameter_names": {
                role + ":" + suffix: PREFIX + suffix
                for role in ROLES
                for suffix in PARAMETERS[role]
            },
            "stop_after": config["stop_after"],
            "termination_mode": config.get("termination_mode", "scheduled"),
        }
    )
    return manifest.model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(export(Aws(config["region"]), config, partial=args.partial), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
