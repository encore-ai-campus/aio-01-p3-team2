"""Bind actual web EIPs to origin settings after the application stack is complete."""

import argparse
import json
from pathlib import Path

from infra.aws.infrastructure import Aws, owned_stack, preflight, validate_config


def endpoints(aws, config):
    preflight(aws, config)
    if config.get("access_mode") != "ip":
        raise ValueError("This operation is for IP access only")
    stack = aws("cloudformation", "describe-stacks", "--stack-name", "ikonly-demo-application")[
        "Stacks"
    ][0]
    owned_stack(stack)
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise ValueError("Application stack must complete before EIP binding")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    config = {
        **config,
        "plan_public_ip": outputs["PlanWebPublicIp"],
        "game_public_ip": outputs["GameWebPublicIp"],
    }
    validate_config(config)
    for key, role in (("plan_public_ip", "PlanWeb"), ("game_public_ip", "GameWeb")):
        addresses = aws("ec2", "describe-addresses", "--public-ips", config[key])["Addresses"]
        if len(addresses) != 1 or addresses[0].get("InstanceId") != outputs[role]:
            raise ValueError("Web EIP is not associated with the selected host")
        tags = {tag["Key"]: tag["Value"] for tag in addresses[0].get("Tags", [])}
        if tags.get("Environment") != "ikonly-demo" or tags.get("ManagedBy") != "ikonly-iac":
            raise ValueError("Web EIP ownership is not verified")
    plan, game = ("https://" + config[key] for key in ("plan_public_ip", "game_public_ip"))
    return {
        "config": config,
        "web": {
            "plan-web": {
                "PUBLIC_HOST": config["plan_public_ip"],
                "WEB_ACCESS_MODE": "ip",
                "HTTPS_MODE": "internal",
            },
            "game-web": {
                "PUBLIC_HOST": config["game_public_ip"],
                "WEB_ACCESS_MODE": "ip",
                "HTTPS_MODE": "internal",
            },
        },
        "origins": {"main-api": plan + "," + game, "fault-api": plan},
        "ca_trust_required": True,
        "application_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    result = endpoints(Aws(config["region"]), config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
