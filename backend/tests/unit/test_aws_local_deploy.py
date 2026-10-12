"""Local AWS operations and reviewed host release guards."""

import copy
import hashlib
import json

import pytest
from infra.aws.host_release import selected_image
from infra.aws.iac import ROLES
from infra.aws.ssm_operations import send, status


def test_host_image_selection_binds_manifest_hash_source_role_and_account():
    sha = "a" * 40
    manifest = {
        "environment_id": "ikonly-demo",
        "source_sha": sha,
        "services": {
            role: {
                "image": "web" if role.endswith("-web") else "application",
                "digest": "sha256:" + "b" * 64,
                "source_sha": sha,
            }
            for role in ROLES
        },
    }
    raw = json.dumps(manifest).encode()
    digest = hashlib.sha256(raw).hexdigest()
    assert selected_image(manifest, "plan-web", "123456789012", sha, digest, raw).endswith(
        "/web@sha256:" + "b" * 64
    )
    with pytest.raises(ValueError):
        selected_image(manifest, "plan-web", "123456789012", sha, "c" * 64, raw)
    for key, value in (("source_sha", "c" * 40), ("environment_id", "other")):
        bad = copy.deepcopy(manifest)
        bad[key] = value
        with pytest.raises(ValueError):
            selected_image(bad, "main-api", "123456789012", sha, digest, raw)
    manifest["services"]["main-api"]["image"] = "web"
    with pytest.raises(ValueError):
        selected_image(manifest, "main-api", "123456789012", sha, digest, raw)


def test_local_ssm_operations_require_owned_running_instance_and_hide_output():
    config = {
        "account_id": "123456789012",
        "region": "ap-northeast-2",
        "environment_id": "ikonly-demo",
        "stop_after": None,
        "termination_mode": "manual",
    }
    tags = [
        {"Key": "Environment", "Value": "ikonly-demo"},
        {"Key": "ManagedBy", "Value": "ikonly-iac"},
    ]
    requests = []

    def aws(*args, **kwargs):
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/operator"}
        if args[:2] == ("ec2", "describe-instances"):
            return {"Reservations": [{"Instances": [{"Tags": tags, "State": {"Name": "running"}}]}]}
        if args[:2] == ("ssm", "send-command"):
            requests.append(kwargs["payload"])
            return {"Command": {"CommandId": "command"}}
        return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "secret"}

    assert send(aws, config, "i-fixture", ["true"]) == "command"
    assert requests[0]["InstanceIds"] == ["i-fixture"]
    assert "secret" not in json.dumps(status(aws, "command", "i-fixture"))
    with pytest.raises(ValueError):
        send(aws, config, "i-fixture", [], document="arbitrary")
    tags[0]["Value"] = "yejin"
    with pytest.raises(ValueError):
        send(aws, config, "i-fixture", ["true"])
    assert len(requests) == 1
