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
        access_mode="domain",
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
    assert database["DeletionPolicy"] == "Delete"
    assert database["UpdateReplacePolicy"] == "Snapshot"
    assert properties["DeleteAutomatedBackups"] is True
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
    placeholder = False

    def fake_aws(*args, **kwargs):
        operation = args[:2]
        if operation == ("sts", "get-caller-identity"):
            return {"Account": config["account_id"], "Arn": "arn:aws:iam::123456789012:user/ops"}
        if operation == ("cloudformation", "describe-change-set"):
            return response
        if operation == ("cloudformation", "get-template"):
            return {"TemplateBody": network()}
        if operation == ("cloudformation", "describe-stacks"):
            if placeholder:
                return {"Stacks": [{"StackStatus": "REVIEW_IN_PROGRESS", "Tags": []}]}
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
    placeholder = True
    with pytest.raises(ValueError):
        review(fake_aws, config, copy.deepcopy(record))
    response["Tags"] = [
        {"Key": "Environment", "Value": "ikonly-demo"},
        {"Key": "ManagedBy", "Value": "ikonly-iac"},
    ]
    assert review(fake_aws, config, copy.deepcopy(record))["status"] == "REVIEWED"
    response["Changes"][0]["ResourceChange"]["Action"] = "Modify"
    with pytest.raises(ValueError):
        review(fake_aws, config, copy.deepcopy(record))
    response["Changes"][0]["ResourceChange"]["Action"] = "Add"
    placeholder = False
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
                        "EvalResourceName": "arn:aws:ssm:${Region}:${Account}:${RelativeId}",
                        "EvalDecision": "implicitDeny",
                        "ResourceSpecificResults": [
                            {
                                "EvalResourceName": arn,
                                "EvalResourceDecision": "allowed"
                                if invalid_scope or arn.split(PREFIX)[1] in PARAMETERS[role]
                                else "implicitDeny",
                            }
                            for arn in payload["ResourceArns"]
                        ],
                    }
                    for action in payload["ActionNames"]
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


def test_ip_config_can_plan_before_allocation_and_requires_distinct_public_ips():
    config = {
        **valid_config(),
        "access_mode": "ip",
        "https_mode": "internal",
        "plan_domain": None,
        "game_domain": None,
    }
    assert validate_config(config)
    assert validate_config({**config, "plan_public_ip": "8.8.8.8", "game_public_ip": "8.8.4.4"})
    for changes in (
        {"https_mode": "acme"},
        {"plan_public_ip": "10.0.0.1", "game_public_ip": "8.8.4.4"},
        {"plan_public_ip": "8.8.8.8"},
        {"plan_public_ip": "8.8.8.8", "game_public_ip": "8.8.8.8"},
        {"plan_domain": "plan.example.com"},
    ):
        with pytest.raises(ValueError):
            validate_config({**config, **changes})


def test_ip_eip_binding_verifies_ownership_and_builds_site_specific_origins():
    from infra.aws.web_endpoints import endpoints

    config = {
        **valid_config(),
        "access_mode": "ip",
        "https_mode": "internal",
        "plan_domain": None,
        "game_domain": None,
    }
    mismatch = False

    def fake_aws(*args, **kwargs):
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/ops"}
        if args[:2] == ("cloudformation", "describe-stacks"):
            return {
                "Stacks": [
                    {
                        "StackStatus": "CREATE_COMPLETE",
                        "Tags": [
                            {"Key": "Environment", "Value": "ikonly-demo"},
                            {"Key": "ManagedBy", "Value": "ikonly-iac"},
                        ],
                        "Outputs": [
                            {"OutputKey": key, "OutputValue": value}
                            for key, value in {
                                "PlanWebPublicIp": "8.8.8.8",
                                "GameWebPublicIp": "8.8.4.4",
                                "PlanWeb": "i-plan",
                                "GameWeb": "i-game",
                            }.items()
                        ],
                    }
                ]
            }
        if args[:2] == ("ec2", "describe-addresses"):
            return {
                "Addresses": [
                    {
                        "InstanceId": "wrong"
                        if mismatch
                        else "i-plan"
                        if args[-1] == "8.8.8.8"
                        else "i-game",
                        "Tags": [
                            {"Key": "Environment", "Value": "ikonly-demo"},
                            {"Key": "ManagedBy", "Value": "ikonly-iac"},
                        ],
                    }
                ]
            }
        pytest.fail("Unexpected operation")

    result = endpoints(fake_aws, config)
    assert result["origins"] == {
        "main-api": "https://8.8.8.8,https://8.8.4.4",
        "fault-api": "https://8.8.8.8",
    }
    assert result["web"]["plan-web"]["HTTPS_MODE"] == "internal"
    mismatch = True
    with pytest.raises(ValueError):
        endpoints(fake_aws, config)


def test_https_origin_accepts_exact_ip_and_rejects_url_fields():
    from backend.app.guards.cloud_access import valid_https_origin

    assert valid_https_origin("https://8.8.8.8")
    assert valid_https_origin("https://plan.localhost:8443")
    for value in (
        "http://8.8.8.8",
        "https://8.8.8.8/path",
        "https://user@8.8.8.8",
        "https://8.8.8.8?x=1",
        "https://8.8.8.8#x",
        "https://8.8.8.8:bad",
        "https://",
        "https://8.8.8.8\\evil",
    ):
        assert not valid_https_origin(value)


@pytest.mark.parametrize("role,port", [("main-api", 8000), ("fault-mcp", 8010)])
def test_fleet_acceptance_checks_actual_private_bind(monkeypatch, role, port):
    import io
    import subprocess
    import urllib.request

    from infra.aws.fleet_release import acceptance_script

    service = {"source_sha": "a" * 40, "digest": "sha256:" + "b" * 64}
    inspected = {
        "State": {"Health": {"Status": "healthy"}},
        "Config": {
            "Labels": {"org.opencontainers.image.revision": service["source_sha"]},
            "Image": "registry/image@" + service["digest"],
            "Env": [],
        },
    }
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda args, **kw: "container" if args[1] == "ps" else json.dumps([inspected]),
    )
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kw: (
            json.dumps({"private_ip": "10.40.1.12"})
            if path == Path("/etc/eventquest/deployment.json")
            else original_read(path, *args, **kw)
        ),
    )
    urls = []

    def ready(url, **kw):
        urls.append(url)
        result = io.BytesIO(b'{"status":"ready"}')
        result.status = 200
        return result

    monkeypatch.setattr(urllib.request, "urlopen", ready)
    script = acceptance_script(role, service)
    python = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    exec(compile(python, "<private-readiness-check>", "exec"), {})  # noqa: S102 - repository-generated script with isolated IO.
    assert urls == [f"http://10.40.1.12:{port}/health/ready"]


def test_initial_resume_refuses_app_parameters_and_mismatched_origin(tmp_path):
    from infra.aws.initial_deployment import resume_records
    from infra.aws.operation_state import write

    config = valid_config()
    write(tmp_path / "initial-start.json", {"baseline_empty": True, "lock_token": "original"})
    write(tmp_path / "lock.json", {"token": "original"})
    write(tmp_path / "identity.json", {"config_sha256": digest(config)})
    stacks = [{"StackName": "ikonly-demo-network"}]

    class ParametersOnly:
        def __call__(self, *args):
            assert args[:2] == ("ssm", "describe-parameters")
            return {
                "Parameters": [
                    {"Name": "/multi-agent-autohealing/ikonly-demo/shared/openai-api-key"}
                ]
            }

    with pytest.raises(ValueError, match="pre-service master"):
        resume_records(ParametersOnly(), config, tmp_path, stacks)
    write(tmp_path / "lock.json", {"token": "different-owner"})
    with pytest.raises(ValueError, match="recorded initial"):
        resume_records(ParametersOnly(), config, tmp_path, stacks)


def test_host_command_stdin_preserves_lf_bytes(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from infra.aws.host_release import run

    def execute(args, **kwargs):
        assert kwargs["input"] == b"nonsecret-example\n"
        assert "text" not in kwargs
        return SimpleNamespace(returncode=0, stdout=b"safe-result\n")

    monkeypatch.setattr(subprocess, "run", execute)
    assert run(["example-command"], input_text="nonsecret-example\n") == "safe-result"


def test_explicit_service_update_preserves_other_versions_and_requires_dependencies(monkeypatch):
    from infra.aws import release_plan

    candidate = {
        "environment_id": "ikonly-demo",
        "source_sha": "b" * 40,
        "ci_run_id": "123",
        "ci_attempt": 1,
        "previous_release_id": None,
        **{key + "_checksum": "sha256:" + "a" * 64 for key in ("schema", "migration", "catalog")},
        "services": {
            role: {
                "image": "web" if role.endswith("web") else "application",
                "digest": "sha256:" + "b" * 64,
                "source_sha": "b" * 40,
            }
            for role in ROLES
        },
    }
    previous = copy.deepcopy(candidate)
    previous.update(source_sha="a" * 40, release_id="c" * 40)
    for service in previous["services"].values():
        service.update(source_sha="a" * 40, digest="sha256:" + "a" * 64, deployment_id="c" * 40)
    monkeypatch.setattr(
        release_plan,
        "plan_from_git",
        lambda *args, **kw: {
            "targets": [],
            "migration_required": False,
            "manual_review_required": False,
        },
    )
    selected = release_plan.build(candidate, previous, services=["game-web"])
    assert selected["targets"] == ["game-web"]
    for role in set(ROLES) - {"game-web"}:
        assert selected["release"]["services"][role] == previous["services"][role]
    paired = release_plan.build(candidate, previous, services=["main-api"])
    assert paired["targets"] == ["main-api", "fault-api"]
    candidate["schema_checksum"] = "sha256:" + "d" * 64
    with pytest.raises(ValueError, match="required dependency"):
        release_plan.build(candidate, previous, services=["game-web"])
