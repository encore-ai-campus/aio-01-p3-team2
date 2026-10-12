"""Read-only AWS acceptance checks; no secrets are retrieved and no commands sent."""

import argparse
import json
from pathlib import Path

from infra.aws.contracts import EnvironmentManifest
from infra.aws.iac import PARAMETERS, PREFIX, ROLES
from infra.aws.infrastructure import Aws, preflight, validate_config


def verify(aws, config, manifest):
    preflight(aws, config)
    if (
        manifest.account_id != config["account_id"]
        or manifest.environment_id != config["environment_id"]
    ):
        raise ValueError("Manifest account/environment mismatch")
    ids = list(manifest.roles.values())
    instances = aws("ec2", "describe-instances", "--instance-ids", *ids)
    found = {
        item["InstanceId"]: item
        for reservation in instances["Reservations"]
        for item in reservation["Instances"]
    }
    nodes = aws(
        "ssm",
        "describe-instance-information",
        payload={"Filters": [{"Key": "InstanceIds", "Values": ids}]},
    )
    online = {
        item["InstanceId"]
        for item in nodes["InstanceInformationList"]
        if item["PingStatus"] == "Online"
    }
    all_names = sorted(
        set().union(
            *PARAMETERS.values(),
            {
                "operations/master-password",
                "operations/main-owner-url",
                "operations/fault-owner-url",
            },
        )
    )
    all_arns = [
        "arn:aws:ssm:ap-northeast-2:" + config["account_id"] + ":parameter" + PREFIX + name
        for name in all_names
    ]
    results = {}
    for role in ROLES:
        identifier = manifest.roles[role]
        item = found[identifier]
        tags = {tag["Key"]: tag["Value"] for tag in item.get("Tags", [])}
        if (
            item["State"]["Name"] != "running"
            or tags.get("Environment") != "ikonly-demo"
            or tags.get("ManagedBy") != "ikonly-iac"
            or tags.get("ServiceRole") != role
        ):
            raise ValueError("Host state or ownership mismatch")
        if (
            item["MetadataOptions"]["HttpTokens"] != "required"
            or item["MetadataOptions"]["HttpPutResponseHopLimit"] != 1
        ):
            raise ValueError("Host IMDS protection mismatch")
        if (not role.endswith("web") and item.get("PublicIpAddress")) or identifier not in online:
            raise ValueError("Host isolation or SSM online check failed")
        profile = aws(
            "iam",
            "get-instance-profile",
            "--instance-profile-name",
            item["IamInstanceProfile"]["Arn"].split("/")[-1],
        )["InstanceProfile"]
        if [entry["RoleName"] for entry in profile["Roles"]] != ["ikonly-demo-" + role]:
            raise ValueError("Temporary operations profile has not been restored")
        role_arn = "arn:aws:iam::" + config["account_id"] + ":role/ikonly-demo-" + role
        # IAM simulator has limits on resource count; evaluate in small complete batches.
        decisions = {}
        for offset in range(0, len(all_arns), 10):
            response = aws(
                "iam",
                "simulate-principal-policy",
                payload={
                    "PolicySourceArn": role_arn,
                    "ActionNames": ["ssm:GetParameter", "ssm:GetParameters"],
                    "ResourceArns": all_arns[offset : offset + 10],
                },
            )
            if response.get("IsTruncated"):
                raise ValueError("Incomplete IAM simulation")
            for row in response["EvaluationResults"]:
                specific = row.get("ResourceSpecificResults")
                if specific:
                    for item in specific:
                        decisions[(row["EvalActionName"], item["EvalResourceName"])] = item[
                            "EvalResourceDecision"
                        ]
                else:
                    decisions[(row["EvalActionName"], row["EvalResourceName"])] = row[
                        "EvalDecision"
                    ]
        for name, arn in zip(all_names, all_arns, strict=True):
            for action in ("ssm:GetParameter", "ssm:GetParameters"):
                decision = decisions.get((action, arn))
                expected = name in PARAMETERS[role]
                if decision is None or (decision == "allowed") != expected:
                    raise ValueError("IAM parameter allow/deny boundary failed")
        results[role] = {
            "instance_id": identifier,
            "ssm_online": True,
            "parameter_scope_simulation": "PASS",
        }
    database = aws(
        "rds", "describe-db-instances", "--db-instance-identifier", "ikonly-demo-postgres"
    )["DBInstances"][0]
    if (
        database["DBInstanceStatus"] != "available"
        or database["PubliclyAccessible"]
        or not database["StorageEncrypted"]
        or not database["DeletionProtection"]
    ):
        raise ValueError("RDS availability/privacy/encryption check failed")
    expected_rds = next(
        (resource.id for resource in manifest.resources if resource.kind == "AWS::RDS::DBInstance"),
        None,
    )
    if expected_rds != database["DBInstanceIdentifier"]:
        raise ValueError("RDS manifest identity mismatch")
    return {
        "status": "INFRASTRUCTURE_CHECKED",
        "services": results,
        "rds_private": True,
        "parameter_values_read": False,
        "application_accepted": False,
        "note": "IAM simulation is not a live host decrypt test; TLS/DB roles and app acceptance are separate gates",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--environment-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    manifest = EnvironmentManifest.model_validate_json(
        args.environment_manifest.read_text(encoding="utf-8")
    )
    result = verify(Aws(config["region"]), config, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
