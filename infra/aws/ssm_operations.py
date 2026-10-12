"""Local operations identity: scope SSM commands to verified ikonly hosts."""

from infra.aws.infrastructure import owned_stack, preflight


def send(aws, config, instance_id, commands, *, document="AWS-RunShellScript", parameters=None):
    preflight(aws, config)
    instance = aws("ec2", "describe-instances", "--instance-ids", instance_id)["Reservations"][0][
        "Instances"
    ][0]
    owned_stack(instance)
    if instance["State"]["Name"] != "running":
        raise ValueError("Operations require a running owned host")
    if document not in ("AWS-RunShellScript", "IkonlyDeploy"):
        raise ValueError("Unknown operations document")
    request = {
        "DocumentName": document,
        "InstanceIds": [instance_id],
        "TimeoutSeconds": 600,
        "Parameters": parameters or {"commands": commands, "executionTimeout": ["1800"]},
        "Comment": "ikonly reviewed local deployment operation",
    }
    return aws("ssm", "send-command", payload=request)["Command"]["CommandId"]


def status(aws, command_id, instance_id):
    result = aws(
        "ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", instance_id
    )
    # Callers may persist metadata; credential-bearing stdout/stderr are not reported.
    return {
        key: result.get(key)
        for key in (
            "CommandId",
            "InstanceId",
            "Status",
            "ResponseCode",
            "ExecutionStartDateTime",
            "ExecutionEndDateTime",
        )
    }
