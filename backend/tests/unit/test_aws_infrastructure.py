import copy
import json
from pathlib import Path

import pytest
from infra.aws.iac import PARAMETERS, ROLES, ROOT, access, application, data, generated, network
from infra.aws.infrastructure import check_parameters, digest, owned_stack, validate_config
from infra.aws.parameters import render


def valid_config():
    config = json.loads((ROOT / "config/demo.example.json").read_text())
    config.update(
        account_id="123456789012",
        plan_domain="plan.example.com",
        game_domain="game.example.com",
        operator_cidr="203.0.113.4/32",
        stop_after="2099-01-01T00:00:00+09:00",
        termination_mode="scheduled",
    )
    return config


def test_config_requires_explicit_account_domains_cidr_and_future_stop():
    assert validate_config(valid_config())
    assert validate_config({**valid_config(), "termination_mode": "manual", "stop_after": None})
    for field, value in [
        ("account_id", None),
        ("operator_cidr", "0.0.0.0/0"),
        ("stop_after", "2020-01-01T00:00:00+09:00"),
        ("plan_domain", "plan.localhost"),
        ("auto_deploy_enabled", True),
        ("region", "us-east-1"),
    ]:
        config = valid_config()
        config[field] = value
        with pytest.raises(ValueError):
            validate_config(config)


def test_six_hosts_have_imdsv2_encrypted_disks_and_no_private_public_ip():
    hosts = [
        item["Properties"]
        for item in application()["Resources"].values()
        if item["Type"] == "AWS::EC2::Instance"
    ]
    assert len(hosts) == 6
    assert sum(host["NetworkInterfaces"][0]["AssociatePublicIpAddress"] for host in hosts) == 2
    for host in hosts:
        assert host["MetadataOptions"]["HttpTokens"] == "required"
        assert host["MetadataOptions"]["HttpPutResponseHopLimit"] == 1
        assert host["BlockDeviceMappings"][0]["Ebs"]["Encrypted"]
        assert host["CreditSpecification"]["CPUCredits"] == "standard"
        assert "KeyName" not in host
        assert "DOCKER-USER" in host["UserData"]["Fn::Base64"]


def test_network_has_exact_service_edges_and_no_ssh_or_public_database():
    resources = network()["Resources"]
    ingress = [
        item["Properties"]
        for item in resources.values()
        if item["Type"] == "AWS::EC2::SecurityGroupIngress"
    ]
    assert not any(item["FromPort"] == 22 for item in ingress)
    assert all(
        "SourceSecurityGroupId" in item
        for item in ingress
        if item["FromPort"] in (8000, 8010, 5432)
    )
    assert "GameWebToFaultApiIngress" not in resources
    assert "MainApiToFaultMcpIngress" not in resources
    assert sum(item["Type"] == "AWS::EC2::NatGateway" for item in resources.values()) == 1


def test_rds_is_private_tls_encrypted_and_master_is_versioned_reference():
    database = data()["Resources"]["Database"]
    properties = database["Properties"]
    assert properties["PubliclyAccessible"] is False
    assert properties["StorageEncrypted"] and properties["DeletionProtection"]
    assert properties["ManageMasterUserPassword"] is False
    assert "resolve:ssm-secure" in properties["MasterUserPassword"]["Fn::Sub"]
    assert "MasterPasswordVersion" in properties["MasterUserPassword"]["Fn::Sub"]
    assert database["DeletionPolicy"] == "Snapshot"
    assert data()["Resources"]["Parameters"]["Properties"]["Parameters"]["rds.force_ssl"] == "1"


def test_runtime_parameter_permissions_are_role_specific():
    resources = access()["Resources"]
    for role in ROLES:
        name = "".join(part.title() for part in role.split("-"))
        statements = resources[name + "Role"]["Properties"]["Policies"][0]["PolicyDocument"][
            "Statement"
        ]
        reads = statements[0]
        scopes = [item["Fn::Sub"] for item in reads["Resource"]]
        assert len(scopes) == len(PARAMETERS[role])
        assert not any("operations/" in scope or "*" in scope for scope in scopes)
        assert (any("openai-api-key" in scope for scope in scopes)) == (role == "main-api")
        assert "ssm:GetParametersByPath" not in json.dumps(statements)
        assert "ManagedPolicyArns" not in resources[name + "Role"]["Properties"]
        for statement in statements:
            if any(
                action in ("ssm:GetParameter", "ssm:GetParameters")
                for action in statement["Action"]
            ):
                assert statement["Resource"] != "*"


def test_oidc_disabled_and_deploy_only_fixed_document():
    template = access()
    assert template["Parameters"]["EnableGithubRoles"]["Default"] == "false"
    role = template["Resources"]["DeployGithubRole"]
    assert role["Condition"] == "GithubEnabled"
    text = json.dumps(role)
    assert "environment:ikonly-demo" in text
    assert "document/IkonlyDeploy" in text
    assert "AWS-RunShellScript" not in text
    assert "iam:PassRole" not in text
    with pytest.raises(ValueError):
        check_parameters("access", {"EnableGithubRoles": "true"})


def test_fixed_document_has_only_hash_inputs_no_shell_parameters():
    document = generated()["ssm/IkonlyDeploy.yml"]
    assert set(document["parameters"]) == {"ReleaseId", "ManifestSha256"}
    assert all(spec["interpolationType"] == "ENV_VAR" for spec in document["parameters"].values())
    assert "deploy-release" in json.dumps(document)
    assert "{{" not in json.dumps(document)


def test_host_parameter_rendering_uses_actual_proxy_contract_and_fault_isolation(monkeypatch):
    from backend.app.guards.cloud_access import CloudAccess

    values = {name: "a" * 32 for name in PARAMETERS["fault-api"]}
    values["fault/runtime-database-url"] = (
        "postgresql://fault-runtime:pwd@db.internal/eventquest_fault_test"
    )
    config = {
        "environment": {"APP_ALLOWED_ORIGINS": "https://plan.example.com"},
        "proxy_peers": {"plan-web": ["10.72.0.4"]},
    }
    text = render("fault-api", config, values)
    assert "OPENAI_API_KEY" not in text
    assert "POSTGRES_TEST_URL='" + values["fault/runtime-database-url"] in text
    proxy_line = next(
        line for line in text.splitlines() if line.startswith("CLOUD_PROXY_IDENTITIES=")
    )
    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("SERVICE_ROLE", "fault-api")
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "https://plan.example.com")
    monkeypatch.setenv("CLOUD_PROXY_IDENTITIES", proxy_line.split("=", 1)[1][1:-1])
    assert set(CloudAccess.from_environment().proxies) == {"plan-web"}
    config["environment"]["OPENAI_API_KEY"] = "forbidden"
    with pytest.raises(ValueError):
        render("fault-api", config, values)


def test_parameter_values_are_single_line_and_cannot_cross_role():
    values = {name: "abc" for name in PARAMETERS["game-web"]}
    assert "WEB_PASSWORD_HASH='abc'" in render("game-web", {"environment": {}}, values)
    for invalid in ("abc\nAWS_SECRET_ACCESS_KEY=wrong", "abc\rwrong", "abc\0wrong"):
        bad = copy.deepcopy(values)
        bad["game/basic-auth-hash"] = invalid
        with pytest.raises(ValueError):
            render("game-web", {"environment": {}}, bad)
    values["shared/openai-api-key"] = "forbidden"
    with pytest.raises(ValueError):
        render("game-web", {"environment": {}}, values)


def test_planning_rejects_unknown_parameters_oidc_enablement_and_wrong_engine():
    with pytest.raises(ValueError):
        check_parameters("network", {"OperatorCidr": "0.0.0.0/0"})
    with pytest.raises(ValueError):
        check_parameters("data", {"EngineVersion": "17.1"})
    with pytest.raises(ValueError):
        check_parameters("access", {"Commands": "arbitrary"})
    with pytest.raises(ValueError):
        owned_stack({"Tags": [{"Key": "Environment", "Value": "yejin"}]})
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_generated_sources_match_checked_in_templates():
    for path, expected in generated().items():
        assert json.loads((ROOT / path).read_text()) == expected


def test_preflight_rejects_wrong_account_and_root_without_mutation():
    from infra.aws.infrastructure import preflight

    calls = []

    def wrong_account(*args, **kwargs):
        calls.append(args)
        return {"Account": "000000000000", "Arn": "arn:aws:iam::000000000000:user/test"}

    with pytest.raises(ValueError):
        preflight(wrong_account, valid_config())
    assert calls == [("sts", "get-caller-identity")]
    with pytest.raises(ValueError):
        preflight(
            lambda *args, **kwargs: {
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:root",
            },
            valid_config(),
        )


def test_review_binds_account_stack_parameters_template_and_complete_changes():
    from infra.aws.infrastructure import review

    config = valid_config()
    stack_id = (
        "arn:aws:cloudformation:ap-northeast-2:123456789012:stack/ikonly-demo-network/fixture"
    )
    change_id = (
        "arn:aws:cloudformation:ap-northeast-2:123456789012:changeSet/ikonly-fixture/fixture"
    )
    response = {
        "StackId": stack_id,
        "ChangeSetId": change_id,
        "Status": "CREATE_COMPLETE",
        "ExecutionStatus": "AVAILABLE",
        "Changes": [{"ResourceChange": {"Action": "Add", "LogicalResourceId": "Vpc"}}],
        "Parameters": [{"ParameterKey": "OperatorCidr", "ParameterValue": config["operator_cidr"]}],
    }
    record = {
        "stage": "network",
        "config_sha256": digest(config),
        "stack_id": stack_id,
        "change_set_id": change_id,
        "requested_parameters": {"OperatorCidr": config["operator_cidr"]},
    }

    def fake_aws(*args, **kwargs):
        operation = args[:2]
        if operation == ("sts", "get-caller-identity"):
            return {"Account": config["account_id"], "Arn": "arn:aws:iam::123456789012:user/ops"}
        if operation == ("cloudformation", "describe-change-set"):
            return response
        if operation == ("cloudformation", "get-template"):
            return {"TemplateBody": network()}
        if operation == ("cloudformation", "describe-stacks"):
            return {
                "Stacks": [
                    {
                        "Tags": [
                            {"Key": "Environment", "Value": "ikonly-demo"},
                            {"Key": "ManagedBy", "Value": "ikonly-iac"},
                        ]
                    }
                ]
            }
        pytest.fail("Unexpected operation " + str(operation))

    assert review(fake_aws, config, copy.deepcopy(record))["status"] == "REVIEWED"
    response["NextToken"] = "more"
    with pytest.raises(ValueError):
        review(fake_aws, config, copy.deepcopy(record))
    response.pop("NextToken")
    response["Parameters"][0]["ParameterValue"] = "0.0.0.0/0"
    with pytest.raises(ValueError):
        review(fake_aws, config, copy.deepcopy(record))
    with pytest.raises(ValueError):
        review(fake_aws, config, {**record, "stage": "../../other"})


def test_secret_import_never_overwrites_existing_parameter_or_accepts_unknown_name():
    from infra.aws.put_parameters import import_values, validate_values

    def fake_aws(*args, **kwargs):
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/ops"}
        assert args[:2] == ("ssm", "get-parameters")
        assert kwargs["payload"]["WithDecryption"] is False
        return {"Parameters": [{"Name": "existing", "Value": "encrypted"}]}

    with pytest.raises(ValueError):
        import_values(fake_aws, valid_config(), {"main/llm-record-key": "preserved"})
    with pytest.raises(ValueError):
        validate_values({"another-project/key": "forbidden"})


def test_partial_resource_snapshot_preserves_failed_stack_resource_ids():
    from infra.aws.export_environment import export

    def fake_aws(*args, **kwargs):
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/ops"}
        if args[:2] == ("cloudformation", "describe-stacks"):
            return {
                "Stacks": [
                    {
                        "StackName": "ikonly-demo-network",
                        "StackStatus": "CREATE_FAILED",
                        "Tags": [
                            {"Key": "Environment", "Value": "ikonly-demo"},
                            {"Key": "ManagedBy", "Value": "ikonly-iac"},
                        ],
                    }
                ]
            }
        if args[:2] == ("cloudformation", "list-stack-resources"):
            return {
                "StackResourceSummaries": [
                    {
                        "LogicalResourceId": "Vpc",
                        "ResourceType": "AWS::EC2::VPC",
                        "PhysicalResourceId": "vpc-fixture",
                        "ResourceStatus": "CREATE_COMPLETE",
                    }
                ]
            }
        pytest.fail("Unexpected operation")

    result = export(fake_aws, valid_config(), partial=True)
    assert result["status"] == "PARTIAL_RESOURCE_SNAPSHOT"
    assert result["resources"][0]["id"] == "vpc-fixture"
    assert result["stacks"]["ikonly-demo-application"] == "NOT_CREATED"
    assert result["deployment_authorized"] is False


def test_apply_requires_reviewed_hash_and_does_not_claim_stack_completion(tmp_path, monkeypatch):
    import hashlib
    import sys

    from infra.aws import infrastructure

    config_path, record_path, output = (
        tmp_path / name for name in ("config.json", "reviewed.json", "result.json")
    )
    config_path.write_text(json.dumps(valid_config()))
    record = {
        "stage": "network",
        "status": "REVIEWED",
        "changes_sha256": "a" * 64,
        "change_set_id": "fixture",
        "template_sha256": hashlib.sha256((ROOT / "stacks/network.yml").read_bytes()).hexdigest(),
    }
    record_path.write_text(json.dumps(record))
    calls = []
    monkeypatch.setattr(
        infrastructure, "Aws", lambda region: lambda *args, **kwargs: calls.append(args)
    )
    monkeypatch.setattr(infrastructure, "review", lambda aws, config, record: record)
    argv = [
        "infra",
        "apply",
        "--config",
        str(config_path),
        "--record",
        str(record_path),
        "--output",
        str(output),
        "--approve-sha256",
    ]
    monkeypatch.setattr(sys, "argv", argv + ["b" * 64])
    with pytest.raises(ValueError):
        infrastructure.main()
    assert not calls
    monkeypatch.setattr(sys, "argv", argv + ["a" * 64])
    infrastructure.main()
    assert calls == [("cloudformation", "execute-change-set", "--change-set-name", "fixture")]
    assert json.loads(output.read_text())["status"] == "EXECUTION_REQUESTED"
    assert json.loads(output.read_text())["deployment_authorized"] is False


def test_actual_infrastructure_query_requires_ssm_online_and_opposite_parameter_denial():
    from infra.aws.contracts import EnvironmentManifest
    from infra.aws.iac import PREFIX
    from infra.aws.verify_infrastructure import verify

    role_ids = {role: "i-" + str(index + 1) * 17 for index, role in enumerate(ROLES)}
    manifest = EnvironmentManifest.model_validate(
        {
            "account_id": "123456789012",
            "region": "ap-northeast-2",
            "environment_id": "ikonly-demo",
            "roles": role_ids,
            "parameter_names": {},
            "stop_after": "2099-01-01T00:00:00+09:00",
            "resources": [
                {
                    "id": identifier,
                    "kind": "AWS::EC2::Instance",
                    "owner_stack": "fixture",
                    "environment_id": "ikonly-demo",
                    "retain": False,
                }
                for identifier in role_ids.values()
            ]
            + [
                {
                    "id": "ikonly-demo-postgres",
                    "kind": "AWS::RDS::DBInstance",
                    "owner_stack": "fixture",
                    "environment_id": "ikonly-demo",
                    "retain": False,
                }
            ],
        }
    )
    invalid_scope = False
    manual = EnvironmentManifest.model_validate(
        {**manifest.model_dump(), "termination_mode": "manual", "stop_after": None}
    )
    assert manual.stop_after is None
    with pytest.raises(ValueError):
        EnvironmentManifest.model_validate(
            {**manifest.model_dump(), "termination_mode": "scheduled", "stop_after": None}
        )

    def fake_aws(*args, **kwargs):
        operation = args[:2]
        if operation == ("sts", "get-caller-identity"):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/ops"}
        if operation == ("ec2", "describe-instances"):
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": identifier,
                                "State": {"Name": "running"},
                                "MetadataOptions": {
                                    "HttpTokens": "required",
                                    "HttpPutResponseHopLimit": 1,
                                },
                                "Tags": [
                                    {"Key": "Environment", "Value": "ikonly-demo"},
                                    {"Key": "ManagedBy", "Value": "ikonly-iac"},
                                    {"Key": "ServiceRole", "Value": role},
                                ],
                                "IamInstanceProfile": {
                                    "Arn": "arn:aws:iam::123456789012:instance-profile/" + role
                                },
                            }
                            for role, identifier in role_ids.items()
                        ]
                    }
                ]
            }
        if operation == ("ssm", "describe-instance-information"):
            return {
                "InstanceInformationList": [
                    {"InstanceId": identifier, "PingStatus": "Online"}
                    for identifier in role_ids.values()
                ]
            }
        if operation == ("iam", "get-instance-profile"):
            return {"InstanceProfile": {"Roles": [{"RoleName": "ikonly-demo-" + args[-1]}]}}
        if operation == ("iam", "simulate-principal-policy"):
            payload = kwargs["payload"]
            role = payload["PolicySourceArn"].split("ikonly-demo-")[1]
            return {
                "EvaluationResults": [
                    {
                        "EvalActionName": action,
                        "EvalResourceName": arn,
                        "EvalDecision": "allowed"
                        if invalid_scope or arn.split(PREFIX)[1] in PARAMETERS[role]
                        else "implicitDeny",
                    }
                    for action in payload["ActionNames"]
                    for arn in payload["ResourceArns"]
                ]
            }
        if operation == ("rds", "describe-db-instances"):
            return {
                "DBInstances": [
                    {
                        "DBInstanceStatus": "available",
                        "PubliclyAccessible": False,
                        "StorageEncrypted": True,
                        "DeletionProtection": True,
                        "DBInstanceIdentifier": "ikonly-demo-postgres",
                    }
                ]
            }
        pytest.fail("Unexpected AWS operation")

    assert verify(fake_aws, valid_config(), manifest)["status"] == "INFRASTRUCTURE_CHECKED"
    invalid_scope = True
    with pytest.raises(ValueError):
        verify(fake_aws, valid_config(), manifest)


def test_aws_payload_file_avoids_windows_argv_limit_and_is_deleted(monkeypatch):
    from types import SimpleNamespace

    from infra.aws import infrastructure

    seen = []
    payload = {"TemplateBody": "x" * 50000}

    def fake_run(command, **kwargs):
        assert sum(len(part) for part in command) < 2000
        request = Path(command[-1].removeprefix("file://"))
        assert json.loads(request.read_text(encoding="utf-8")) == payload
        seen.append(request)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')

    monkeypatch.setattr(infrastructure.subprocess, "run", fake_run)
    assert infrastructure.Aws("ap-northeast-2")(
        "cloudformation", "validate-template", payload=payload
    ) == {"ok": True}
    assert seen and not seen[0].exists()
