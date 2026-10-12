"""Account-bound preflight/change-set operations. Planning never executes a change set."""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from infra.aws.iac import ROOT, generated


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_config(config):
    if config.get("region") != "ap-northeast-2" or config.get("environment_id") != "ikonly-demo":
        raise ValueError("Unexpected region/environment")
    if not re.fullmatch(r"\d{12}", config.get("account_id") or ""):
        raise ValueError("Authenticated account ID is required")
    access_mode = config.get("access_mode", "domain")
    if access_mode == "ip":
        if config.get("https_mode") != "internal":
            raise ValueError("IP access requires explicitly managed internal CA certificates")
        if config.get("plan_domain") or config.get("game_domain"):
            raise ValueError("IP configuration cannot also select domains")
        hosts = [config.get(name) for name in ("plan_public_ip", "game_public_ip")]
        if any(hosts) and not all(hosts):
            raise ValueError("Assign both web EIPs together")
        if all(hosts):
            for host in hosts:
                address = ipaddress.ip_address(host)
                if address.version != 4 or not address.is_global or str(address) != host:
                    raise ValueError("Web endpoints require canonical public IPv4 addresses")
            if hosts[0] == hosts[1]:
                raise ValueError("Web services require distinct EIPs")
        # Initial infrastructure planning precedes allocation of web EIPs.
    elif access_mode == "domain":
        for name in ("plan_domain", "game_domain"):
            value = config.get(name) or ""
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.[a-z]+", value) or value.endswith(
                (".internal", ".localhost")
            ):
                raise ValueError("Public domain is required: " + name)
        if config["plan_domain"] == config["game_domain"]:
            raise ValueError("Sites require distinct domains")
    else:
        raise ValueError("Unknown access mode")
    cidr = ipaddress.ip_network(config.get("operator_cidr") or "", strict=True)
    if cidr.version != 4 or cidr.prefixlen == 0:
        raise ValueError("Operator IPv4 CIDR must not expose HTTPS to the whole Internet")
    mode = config.get("termination_mode", "scheduled")
    if mode == "manual":
        if config.get("stop_after") is not None:
            raise ValueError("Manual termination must have stop_after=null")
    elif mode == "scheduled":
        stop = datetime.fromisoformat(config.get("stop_after") or "")
        if stop.tzinfo is None or stop <= datetime.now(UTC):
            raise ValueError("A future stop_after with timezone is required")
    else:
        raise ValueError("Unknown termination mode")
    if config.get("auto_deploy_enabled") is not False:
        raise ValueError("Initial automatic deployment must remain disabled")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", config.get("notification_email") or ""):
        raise ValueError("Notification email is required")
    if config.get("parameter_prefix") != "/multi-agent-autohealing/ikonly-demo/":
        raise ValueError("Unexpected parameter prefix")
    if config.get("rds") != {
        "engine": "postgres",
        "instance_class": "db.t4g.small",
        "public": False,
    }:
        raise ValueError("RDS specification differs from the reviewed design")
    expected = {
        role: "t3.medium" if role.endswith("api") else "t3.small"
        for role in ("plan-web", "game-web", "main-api", "fault-api", "main-mcp", "fault-mcp")
    }
    if config.get("instances") != expected:
        raise ValueError("Instance specification differs from the reviewed design")
    return config


class Aws:
    def __init__(self, region):
        self.region = region

    def __call__(self, *args, payload=None):
        command = ["aws", "--region", self.region, "--no-cli-pager", "--output", "json", *args]
        filename = None
        try:
            if payload is not None:
                # Windows CreateProcess has a 32K argv limit. CFN template bodies
                # use a private temporary file, also keeping values out of argv.
                fd, filename = tempfile.mkstemp(prefix="ikonly-aws-request-", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload))
                command += ["--cli-input-json", "file://" + filename]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
        finally:
            if filename is not None:
                os.unlink(filename)
        if result.returncode:
            # AWS errors can include submitted values. Never repeat stderr/payload.
            raise RuntimeError("AWS operation failed: " + " ".join(args[:2]))
        return json.loads(result.stdout or "{}")


def owned_stack(stack):
    tags = {tag["Key"]: tag["Value"] for tag in stack.get("Tags", [])}
    if tags.get("Environment") != "ikonly-demo" or tags.get("ManagedBy") != "ikonly-iac":
        raise ValueError("Existing stack ownership is not verified")


def check_parameters(stage, parameters):
    definition = generated()["stacks/" + stage + ".yml"]["Parameters"]
    if set(parameters) - set(definition):
        raise ValueError("Unknown template parameters")
    if any(key not in parameters and "Default" not in spec for key, spec in definition.items()):
        raise ValueError("Required template parameters are missing")
    if stage == "access" and parameters.get("EnableGithubRoles", "false") != "false":
        raise ValueError(
            "OIDC role enablement requires a separate verified GitHub protection review"
        )
    if stage == "network":
        network = ipaddress.ip_network(parameters["OperatorCidr"], strict=True)
        if network.version != 4 or network.prefixlen == 0:
            raise ValueError("Unsafe operator CIDR")
    if stage == "data":
        if not re.fullmatch(r"16\.\d+", parameters["EngineVersion"]):
            raise ValueError("Verified PostgreSQL 16 version required")
        if (
            not str(parameters["MasterPasswordVersion"]).isdigit()
            or int(parameters["MasterPasswordVersion"]) < 1
        ):
            raise ValueError("Pinned SecureString version required")
    if stage == "application" and not re.fullmatch(r"ami-[a-f0-9]{8,17}", parameters["AmiId"]):
        raise ValueError("Verified Ubuntu x86_64 AMI required")


def changes_fingerprint(response):
    return digest(
        {
            key: response.get(key)
            for key in (
                "ChangeSetId",
                "StackId",
                "ChangeSetType",
                "Parameters",
                "Changes",
                "Capabilities",
                "Tags",
            )
        }
    )


def preflight(aws, config):
    identity = aws("sts", "get-caller-identity")
    if identity["Account"] != config["account_id"]:
        raise ValueError("Authenticated account differs from the selected account")
    if identity.get("Arn", "").endswith(":root"):
        raise ValueError("Use an operations IAM identity instead of account root")
    return {
        "account_id": identity["Account"],
        "region": config["region"],
        "environment_id": config["environment_id"],
        "config_sha256": digest(config),
        "principal_arn": identity["Arn"],
        "started_at": datetime.now(UTC).isoformat(),
        "stop_after": config["stop_after"],
        "termination_mode": config.get("termination_mode", "scheduled"),
        "auto_deploy_enabled": False,
        "deployment_authorized": False,
    }


def plan(aws, config, stage, parameters):
    record = preflight(aws, config)
    check_parameters(stage, parameters)
    if stage == "network" and parameters["OperatorCidr"] != config["operator_cidr"]:
        raise ValueError("Network CIDR differs from selected configuration")
    if stage == "observability" and parameters["NotificationEmail"] != config["notification_email"]:
        raise ValueError("SNS email differs from selected configuration")
    if stage == "application":
        images = aws("ec2", "describe-images", "--image-ids", parameters["AmiId"])["Images"]
        if (
            len(images) != 1
            or images[0].get("OwnerId") != "099720109477"
            or images[0].get("Architecture") != "x86_64"
            or "ubuntu-noble-24.04" not in images[0].get("Name", "")
        ):
            raise ValueError("AMI must be official Ubuntu 24.04 amd64")
    if stage == "data":
        engines = aws(
            "rds",
            "describe-db-engine-versions",
            "--engine",
            "postgres",
            "--engine-version",
            parameters["EngineVersion"],
        )["DBEngineVersions"]
        if len(engines) != 1 or engines[0].get("DBParameterGroupFamily") != "postgres16":
            raise ValueError("RDS version/family is unavailable")
        # Metadata only. Master value never enters this process/template/plan.
        metadata = aws(
            "ssm",
            "describe-parameters",
            "--parameter-filters",
            "Key=Name,Values=" + config["parameter_prefix"] + "operations/master-password",
        )["Parameters"]
        if (
            len(metadata) != 1
            or metadata[0]["Type"] != "SecureString"
            or metadata[0]["Version"] != int(parameters["MasterPasswordVersion"])
        ):
            raise ValueError("Master SecureString version is not verified")
    stacks = aws("cloudformation", "describe-stacks").get("Stacks", [])
    name = "ikonly-demo-" + stage
    existing = next((stack for stack in stacks if stack["StackName"] == name), None)
    if existing:
        owned_stack(existing)
        if existing["StackStatus"] not in (
            "CREATE_COMPLETE",
            "UPDATE_COMPLETE",
            "UPDATE_ROLLBACK_COMPLETE",
            "REVIEW_IN_PROGRESS",
        ):
            raise ValueError("Stack is not ready for a change set")
    path = ROOT / "stacks" / (stage + ".yml")
    body = path.read_text(encoding="utf-8")
    if json.loads(body) != generated()["stacks/" + stage + ".yml"]:
        raise ValueError("Generated template is stale")
    aws("cloudformation", "validate-template", payload={"TemplateBody": body})
    request = {
        "StackName": name,
        "ChangeSetName": "ikonly-"
        + digest(parameters)[:12]
        + "-"
        + datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
        "ChangeSetType": "UPDATE"
        if existing and existing["StackStatus"] != "REVIEW_IN_PROGRESS"
        else "CREATE",
        "TemplateBody": body,
        "Capabilities": ["CAPABILITY_NAMED_IAM"],
        "Parameters": [
            {"ParameterKey": key, "ParameterValue": str(value)}
            for key, value in sorted(parameters.items())
        ],
        "Tags": [
            {"Key": "Environment", "Value": "ikonly-demo"},
            {"Key": "ManagedBy", "Value": "ikonly-iac"},
        ],
    }
    created = aws("cloudformation", "create-change-set", payload=request)
    record.update(
        stage=stage,
        change_set_id=created["Id"],
        stack_id=created["StackId"],
        parameters_sha256=digest(parameters),
        requested_parameters={key: str(value) for key, value in parameters.items()},
        template_sha256=hashlib.sha256(body.encode()).hexdigest(),
        status="PENDING_REVIEW",
        deployment_authorized=False,
    )
    return record


def review(aws, config, record):
    preflight(aws, config)
    if record["config_sha256"] != digest(config):
        raise ValueError("Configuration changed since planning")
    if record.get("stage") not in ("network", "observability", "access", "data", "application"):
        raise ValueError("Unknown stage in recorded plan")
    stack_prefix = (
        "arn:aws:cloudformation:ap-northeast-2:"
        + config["account_id"]
        + ":stack/ikonly-demo-"
        + record["stage"]
        + "/"
    )
    if not record["stack_id"].startswith(stack_prefix):
        raise ValueError("Recorded stack does not match the selected stage/account")
    expected = (
        "arn:aws:cloudformation:ap-northeast-2:" + config["account_id"] + ":changeSet/ikonly-"
    )
    if not record["change_set_id"].startswith(expected):
        raise ValueError("Change set is outside the selected account/region/name")
    response = aws(
        "cloudformation", "describe-change-set", "--change-set-name", record["change_set_id"]
    )
    if response["StackId"] != record["stack_id"]:
        raise ValueError("Change set target changed")
    actual_parameters = {
        item["ParameterKey"]: item.get("ParameterValue") for item in response.get("Parameters", [])
    }
    check_parameters(record["stage"], actual_parameters)
    if (
        record["stage"] == "network"
        and actual_parameters["OperatorCidr"] != config["operator_cidr"]
    ):
        raise ValueError("AWS network CIDR differs from selected configuration")
    if (
        record["stage"] == "observability"
        and actual_parameters["NotificationEmail"] != config["notification_email"]
    ):
        raise ValueError("AWS SNS email differs from selected configuration")
    if any(
        actual_parameters.get(key) != value for key, value in record["requested_parameters"].items()
    ):
        raise ValueError("Recorded parameters differ from the AWS change set")
    actual_template = aws(
        "cloudformation",
        "get-template",
        "--change-set-name",
        record["change_set_id"],
        "--stack-name",
        record["stack_id"],
    )["TemplateBody"]
    if isinstance(actual_template, str):
        actual_template = json.loads(actual_template)
    if digest(actual_template) != digest(generated()["stacks/" + record["stage"] + ".yml"]):
        raise ValueError("AWS change-set template differs from the current reviewed source")
    owned_stack(
        aws("cloudformation", "describe-stacks", "--stack-name", record["stack_id"])["Stacks"][0]
    )
    if (
        response["Status"] != "CREATE_COMPLETE"
        or response["ExecutionStatus"] != "AVAILABLE"
        or response.get("NextToken")
    ):
        raise ValueError("Change set is not completely available for review")
    record.update(
        changes=response["Changes"], changes_sha256=changes_fingerprint(response), status="REVIEWED"
    )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["preflight", "plan", "review", "apply"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--stage", choices=["network", "observability", "access", "data", "application"]
    )
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--approve-sha256")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    aws = Aws(config["region"])
    if args.operation == "preflight":
        result = preflight(aws, config)
    elif args.operation == "plan":
        if not args.stage or not args.parameters:
            parser.error("plan needs --stage and --parameters")
        result = plan(
            aws, config, args.stage, json.loads(args.parameters.read_text(encoding="utf-8"))
        )
    else:
        if not args.record:
            parser.error("review/apply needs --record")
        previous = json.loads(args.record.read_text(encoding="utf-8"))
        result = review(aws, config, dict(previous))
        if args.operation == "apply":
            if (
                previous.get("status") != "REVIEWED"
                or not args.approve_sha256
                or args.approve_sha256 != previous.get("changes_sha256")
                or args.approve_sha256 != result["changes_sha256"]
            ):
                raise ValueError("Reviewed change-set hash must match explicit approval")
            stage = previous["stage"]
            body = (ROOT / "stacks" / (stage + ".yml")).read_bytes()
            if hashlib.sha256(body).hexdigest() != previous["template_sha256"]:
                raise ValueError("Template changed after planning")
            aws(
                "cloudformation", "execute-change-set", "--change-set-name", result["change_set_id"]
            )
            result.update(status="EXECUTION_REQUESTED", deployment_authorized=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result["finished_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Infrastructure operation recorded; inspect status and changes in the output file")


if __name__ == "__main__":
    main()
