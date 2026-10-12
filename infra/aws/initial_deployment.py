"""Local first deployment with reviewed policy and local OpenAI key supply."""

import base64
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

from backend.app.config.cloud_database import DATABASES
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from infra.aws.export_environment import export
from infra.aws.fleet_release import deploy
from infra.aws.host_release import run
from infra.aws.iac import PREFIX, ROLES, ROOT, generated, logical
from infra.aws.infrastructure import digest, owned_stack, plan, review
from infra.aws.operation_state import commit_state, write
from infra.aws.publish_release import publish
from infra.aws.put_parameters import import_values
from infra.aws.web_endpoints import endpoints


def stack_outputs(aws, stage):
    value = aws("cloudformation", "describe-stacks", "--stack-name", "ikonly-demo-" + stage)[
        "Stacks"
    ][0]
    owned_stack(value)
    return {x["OutputKey"]: x["OutputValue"] for x in value.get("Outputs", [])}


def resume_records(aws, config, folder, stacks):
    folder = Path(folder)
    marker = json.loads((folder / "initial-start.json").read_text())
    identity = json.loads((folder / "identity.json").read_text())
    previous_lock = json.loads((folder / "lock.json").read_text())
    if (
        not marker.get("baseline_empty")
        or marker["lock_token"] != previous_lock["token"]
        or identity["config_sha256"] != digest(config)
        or (folder / "execution.json").exists()
        or not stacks
        or any(
            s["StackName"]
            not in {
                "ikonly-demo-" + x
                for x in ("network", "access", "data", "application", "observability")
            }
            for s in stacks
        )
    ):
        raise ValueError("Resume is limited to this recorded initial pre-service infrastructure")
    result = {}
    parameters = aws(
        "ssm",
        "describe-parameters",
        "--parameter-filters",
        "Key=Name,Option=BeginsWith,Values=" + PREFIX,
    )
    if parameters.get("NextToken") or {p["Name"] for p in parameters["Parameters"]} != {
        PREFIX + "operations/master-password"
    }:
        raise ValueError("Resume is limited to pre-service master configuration")
    for stack in stacks:
        owned_stack(stack)
        if (
            stack["StackStatus"] not in ("CREATE_IN_PROGRESS", "CREATE_COMPLETE")
            or stack.get("RoleARN") != config["cloudformation_role_arn"]
        ):
            raise ValueError("Resume requires the original initial creation and service role")
        stage = stack["StackName"].removeprefix("ikonly-demo-")
        record = json.loads((folder / (stage + "-plan.json")).read_text())
        reviewed = json.loads((folder / (stage + "-review.json")).read_text())
        if (
            stack["StackId"] != record["stack_id"]
            or reviewed["stack_id"] != record["stack_id"]
            or any(c["ResourceChange"]["Action"] != "Add" for c in reviewed["changes"])
            or record["template_sha256"]
            != hashlib.sha256(
                (ROOT / "stacks" / (stage + ".yml")).read_text(encoding="utf-8").encode()
            ).hexdigest()
        ):
            raise ValueError("Resume stack differs from its reviewed creation record")
        template = aws("cloudformation", "get-template", "--stack-name", stack["StackId"])[
            "TemplateBody"
        ]
        if isinstance(template, str):
            template = json.loads(template)
        if template != generated()["stacks/" + stage + ".yml"]:
            raise ValueError("Resume template differs from current reviewed infrastructure")
        result[stage] = (record, reviewed)
    return result


def create_stack(aws, config, stage, parameters, output, *, existing=None):
    if existing is not None:
        record, reviewed = existing
        if record["requested_parameters"] != {k: str(v) for k, v in parameters.items()}:
            raise ValueError("Resume parameters differ from the reviewed creation")
        write(output / (stage + "-plan.json"), record)
        write(output / (stage + "-review.json"), reviewed)
        return wait_stack(aws, stage, record, output)
    record = plan(aws, config, stage, parameters)
    write(output / (stage + "-plan.json"), record)
    deadline = time.monotonic() + 300
    while True:
        current = aws(
            "cloudformation", "describe-change-set", "--change-set-name", record["change_set_id"]
        )
        if current["Status"] == "CREATE_COMPLETE":
            break
        if current["Status"] == "FAILED" or time.monotonic() > deadline:
            raise RuntimeError("Initial change set preparation failed")
        time.sleep(3)
    reviewed = review(aws, config, record)
    if any(x["ResourceChange"]["Action"] != "Add" for x in reviewed["changes"]):
        raise ValueError("First deployment refuses modifications to existing resources")
    write(output / (stage + "-review.json"), reviewed)
    aws("cloudformation", "execute-change-set", "--change-set-name", reviewed["change_set_id"])
    return wait_stack(aws, stage, record, output)


def wait_stack(aws, stage, record, output):
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        current = aws("cloudformation", "describe-stacks", "--stack-name", record["stack_id"])[
            "Stacks"
        ][0]
        if current["StackStatus"] == "CREATE_COMPLETE":
            return stack_outputs(aws, stage)
        if not current["StackStatus"].endswith("IN_PROGRESS") and (
            "FAILED" in current["StackStatus"] or "ROLLBACK" in current["StackStatus"]
        ):
            write(
                output / (stage + "-failure.json"),
                aws("cloudformation", "describe-stack-events", "--stack-name", record["stack_id"]),
            )
            raise RuntimeError(
                "Initial stack creation failed; use reset for owned partial resources"
            )
        time.sleep(10)
    raise TimeoutError("Initial stack creation deadline exceeded")


def host_configs(config, outputs, web, endpoint, env):
    result = {}
    for role in ROLES:
        if role.endswith("web"):
            values = {
                **web["web"][role],
                "WEB_USERNAME": "operator",
                "MAIN_API_UPSTREAM": "http://" + outputs["MainApiPrivateIp"] + ":8000",
            }
            if role == "plan-web":
                values["FAULT_API_UPSTREAM"] = "http://" + outputs["FaultApiPrivateIp"] + ":8000"
            result[role] = {"environment": values}
            continue
        purpose = "fault" if role.startswith("fault") else "main"
        spec = DATABASES[purpose]
        values = {
            "RDS_EXPECTED_ENDPOINT": endpoint,
            "EXPECTED_DATABASE_ROLE": spec.reader if role.endswith("mcp") else spec.runtime,
            "DATABASE_AUTO_CREATE": "false",
        }
        if role.endswith("mcp"):
            values.update(
                MCP_HOST="0.0.0.0",
                MCP_PORT="8010",
                MCP_ALLOWED_HOSTS=outputs[logical(role) + "PrivateIp"] + ":8010",
                MCP_ALLOWED_ORIGINS="http://"
                + outputs[logical(purpose + "-api") + "PrivateIp"]
                + ":8000",
            )
        else:
            values.update(
                MCP_URL="http://" + outputs[logical(purpose + "-mcp") + "PrivateIp"] + ":8010/mcp",
                APP_ALLOWED_ORIGINS=web["origins"][role],
                OPERATOR_ACTOR_ID=env["OPERATOR_ACTOR_ID"],
                APPROVED_ACTOR_IDS=env["APPROVED_ACTOR_IDS"],
            )
            if purpose == "main":
                values.update(
                    CANONICAL_PLANNING_MODE="live",
                    WORKFLOW_ENGINE="agent_live",
                    ENABLE_FAULT_INJECTION="false",
                )
                for key in (
                    "OPENAI_MODEL",
                    "LLM_MAX_INPUT_TOKENS",
                    "LLM_MAX_OUTPUT_TOKENS",
                    "LLM_MAX_COST_USD",
                    "LLM_INPUT_USD_PER_MILLION",
                    "LLM_OUTPUT_USD_PER_MILLION",
                    "LLM_PRICING_VERSION",
                    "LLM_COST_CURRENCY",
                ):
                    values[key] = env[key]
            else:
                values.update(
                    CANONICAL_PLANNING_MODE="deterministic",
                    WORKFLOW_ENGINE="agent_mcp",
                    ENABLE_FAULT_INJECTION="true",
                    FAULT_LAB_MODE="isolated",
                    FAULT_LAB_JOURNAL_DIR="/var/lib/eventquest/fault-lab",
                )
        cfg = {"environment": values}
        if role.endswith("api"):
            cfg["proxy_peers"] = {
                peer: [outputs[logical(peer) + "PrivateIp"]]
                for peer in (["plan-web"] if purpose == "fault" else ["plan-web", "game-web"])
            }
        result[role] = cfg
    return result


def initial(
    aws,
    config,
    directory,
    run_id,
    env_file,
    policy_file,
    approved_policy_hash,
    output,
    *,
    resume_from=None,
):
    output = Path(output)
    stacks = [
        s
        for s in aws("cloudformation", "describe-stacks")["Stacks"]
        if s["StackName"].startswith("ikonly-demo-")
    ]
    resumed = resume_records(aws, config, resume_from, stacks) if resume_from else {}
    if stacks and not resumed:
        raise ValueError("First deployment requires reset of existing owned application stacks")
    from infra.aws.cleanup import verify_reset

    if not resumed and any(verify_reset(aws, include_lifecycle=False).values()):
        raise ValueError("First deployment refuses leftover application resources")
    if not config.get("cloudformation_role_arn"):
        raise ValueError("First deployment requires the bootstrapped CloudFormation service role")
    env = dotenv_values(env_file)
    required = {
        "OPENAI_API_KEY",
        "OPERATOR_ACTOR_ID",
        "APPROVED_ACTOR_IDS",
        "OPENAI_MODEL",
        "LLM_MAX_INPUT_TOKENS",
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_MAX_COST_USD",
        "LLM_INPUT_USD_PER_MILLION",
        "LLM_OUTPUT_USD_PER_MILLION",
        "LLM_PRICING_VERSION",
        "LLM_COST_CURRENCY",
    }
    if any(not env.get(key) for key in required):
        raise ValueError("Local live deployment environment is incomplete")
    policy = Path(policy_file).read_bytes()
    if hashlib.sha256(policy).hexdigest() != approved_policy_hash:
        raise ValueError("Explicitly approved policy file hash mismatch")
    from backend.app.services.recovery_policy import validate_policy

    if validate_policy(json.loads(policy)):
        raise ValueError("Initial approved policy is not valid")
    # Verify remote successful CI/artifact identity BEFORE any paid infrastructure.
    from infra.aws.publish_release import verify

    metadata = json.loads(
        run(["gh", "api", f"repos/ik-e/multi-agent-autohealing/actions/runs/{run_id}"])
    )
    jobs = json.loads(
        run(
            [
                "gh",
                "api",
                f"repos/ik-e/multi-agent-autohealing/actions/runs/{run_id}/attempts/{metadata['run_attempt']}/jobs",
                "--paginate",
                "--slurp",
            ]
        )
    )
    candidate = verify(directory, metadata, [j for page in jobs for j in page["jobs"]])
    schema = json.loads(
        run(["git", "show", candidate["source_sha"] + ":infra/aws/schemas/release.schema.json"])
    )
    if "release_id" not in schema["properties"]:
        raise ValueError(
            "CI candidate predates the selective deployment protocol; build the current source"
        )
    lock = json.loads((output / "lock.json").read_text())
    write(
        output / "initial-start.json",
        {
            "baseline_empty": True,
            "lock_token": lock["token"],
            "source_sha": candidate["source_sha"],
            "run_id": run_id,
            "resumed_from": str(resume_from) if resume_from else None,
        },
    )
    network = create_stack(
        aws,
        config,
        "network",
        {"OperatorCidr": config["operator_cidr"]},
        output,
        existing=resumed.get("network"),
    )
    access = create_stack(
        aws,
        config,
        "access",
        {"EnableGithubRoles": "false", "GithubOidcProviderArn": ""},
        output,
        existing=resumed.get("access"),
    )
    if resumed:
        parameter = aws(
            "ssm",
            "get-parameter",
            "--name",
            PREFIX + "operations/master-password",
            "--with-decryption",
        )["Parameter"]
        if parameter["Type"] != "SecureString" or parameter["Version"] != 1:
            raise ValueError("Resume requires the original pinned master parameter")
        master = parameter["Value"]
    else:
        master = secrets.token_urlsafe(32)
        import_values(aws, config, {"operations/master-password": master})
    engine = config.get("engine_version", "16.15")
    data = create_stack(
        aws,
        config,
        "data",
        {
            "DbA": network["DbA"],
            "DbB": network["DbB"],
            "DatabaseSg": network["DatabaseSg"],
            "EngineVersion": engine,
            "MasterPasswordVersion": 1,
        },
        output,
        existing=resumed.get("data"),
    )
    ami = (
        (resumed.get("application") or ({},))[0].get("requested_parameters", {}).get("AmiId")
        or config.get("ami_id")
        or aws(
            "ssm",
            "get-parameter",
            "--name",
            "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
        )["Parameter"]["Value"]
    )
    parameters = {"Public": network["Public"], "App": network["App"], "AmiId": ami}
    for role in ROLES:
        for suffix, source in (("Sg", network), ("Profile", access)):
            parameters[logical(role) + suffix] = source[logical(role) + suffix]
    outputs = create_stack(
        aws, config, "application", parameters, output, existing=resumed.get("application")
    )
    create_stack(
        aws,
        config,
        "observability",
        {
            "NotificationEmail": config["notification_email"],
            **{logical(role) + "Instance": outputs[logical(role)] for role in ROLES},
        },
        output,
        existing=resumed.get("observability"),
    )
    web = endpoints(aws, config)
    config = web["config"]
    manifest = export(aws, config)
    write(output / "environment.json", manifest)
    state = {
        "lifecycle": "INITIALIZING",
        "config": config,
        "roles": manifest["roles"],
        "endpoint": data["Endpoint"],
        "release": None,
    }
    commit_state(aws, state)
    publish(config, directory, run_id, output / "candidate-release.json")
    release = json.loads((output / "candidate-release.json").read_text())
    assert release["source_sha"] == candidate["source_sha"]
    role_passwords = {
        user: secrets.token_urlsafe(32)
        for spec in DATABASES.values()
        for user in (spec.owner, spec.runtime, spec.reader)
    }

    def url(user, password, database):
        return f"postgresql+psycopg://{user}:{quote(password, safe='')}@{data['Endpoint']}:5432/{database}?sslmode=verify-full&sslrootcert=/opt/certs/rds-ca.pem"

    values = {
        "operations/master-url": url("eventquest_bootstrap", master, "postgres"),
        "operations/role-passwords": json.dumps(role_passwords, separators=(",", ":")),
        "shared/openai-api-key": env["OPENAI_API_KEY"],
    }
    values.update(
        {
            "proof/" + name: secrets.token_urlsafe(32)
            for name in ("plan-main", "plan-fault", "game-main")
        }
    )
    for purpose, spec in DATABASES.items():
        values.update(
            {
                purpose + "/runtime-database-url": url(
                    spec.runtime, role_passwords[spec.runtime], spec.database
                ),
                purpose + "/mcp-database-url": url(
                    spec.reader, role_passwords[spec.reader], spec.database
                ),
                "operations/" + purpose + "-owner-url": url(
                    spec.owner, role_passwords[spec.owner], spec.database
                ),
                purpose + "/llm-record-key": Fernet.generate_key().decode(),
            }
        )
    password = secrets.token_urlsafe(20)
    hashed = run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--entrypoint",
            "caddy",
            "ikonly-web:ci",
            "hash-password",
        ],
        input_text=password + "\n",
    )
    values.update({"plan/basic-auth-hash": hashed, "game/basic-auth-hash": hashed})
    import_values(aws, config, values)
    login = output / "web-login.txt"
    login.write_text("username: operator\npassword: " + password + "\n", encoding="utf-8")
    login.chmod(0o600)
    deadline = time.monotonic() + 900
    while True:
        nodes = aws("ssm", "describe-instance-information")["InstanceInformationList"]
        online = {n["InstanceId"] for n in nodes if n["PingStatus"] == "Online"}
        if set(state["roles"].values()) <= online:
            break
        if time.monotonic() > deadline:
            raise TimeoutError("New host SSM readiness deadline")
        time.sleep(10)
    from infra.aws.release_plan import build

    return deploy(
        aws,
        config,
        state,
        build(release),
        output / "fleet",
        configs=host_configs(config, outputs, web, data["Endpoint"], env),
        policy=policy,
        database_config={"actor": env["OPERATOR_ACTOR_ID"], "policy_hash": approved_policy_hash},
        post_acceptance=lambda: public_web_acceptance(aws, config, state, password, output),
    )


def public_web_acceptance(aws, config, state, password, output):
    """Temporarily allow only the hosted runner /32, removing it on all exits."""
    additions = []
    try:
        if os.getenv("GITHUB_ACTIONS") == "true":
            import ipaddress

            with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=20) as response:
                cidr = str(ipaddress.IPv4Address(response.read().decode().strip())) + "/32"
            if cidr != config["operator_cidr"]:
                for role in ("plan-web", "game-web"):
                    node = aws("ec2", "describe-instances", "--instance-ids", state["roles"][role])[
                        "Reservations"
                    ][0]["Instances"][0]
                    for group in node["SecurityGroups"]:
                        permissions = [
                            {
                                "IpProtocol": "tcp",
                                "FromPort": 443,
                                "ToPort": 443,
                                "IpRanges": [
                                    {"CidrIp": cidr, "Description": "Bounded Actions acceptance"}
                                ],
                            }
                        ]
                        aws(
                            "ec2",
                            "authorize-security-group-ingress",
                            payload={"GroupId": group["GroupId"], "IpPermissions": permissions},
                        )
                        additions.append((group["GroupId"], permissions))
        _public_web_acceptance(aws, config, state, password, output)
    finally:
        for group, permissions in additions:
            aws(
                "ec2",
                "revoke-security-group-ingress",
                payload={"GroupId": group, "IpPermissions": permissions},
            )


def _public_web_acceptance(aws, config, state, password, output):
    from infra.aws.host_transport import command

    for role in ("plan-web", "game-web"):
        instance = state["roles"][role]
        metadata = command(
            aws,
            config,
            instance,
            "base64 -w0 /var/lib/eventquest/caddy-data/caddy/pki/authorities/local/root.crt",
            output / (role + "-certificate.json"),
        )
        result = aws(
            "ssm",
            "get-command-invocation",
            "--command-id",
            metadata["command_id"],
            "--instance-id",
            instance,
        )
        certificate = base64.b64decode(result["StandardOutputContent"].strip(), validate=True)
        if not certificate.startswith(b"-----BEGIN CERTIFICATE-----"):
            raise ValueError("Public certificate export format mismatch")
        (output / (role + "-root.crt")).write_bytes(certificate)
        context = ssl.create_default_context(cafile=str(output / (role + "-root.crt")))
        username = "operator"
        auth = base64.b64encode((username + ":" + password).encode()).decode()
        paths = ["/", "/api/game/state" if role == "plan-web" else "/game-api/state"]
        if role == "plan-web":
            paths.append("/api/test/fault-catalog")
        for path in paths:
            host = config["plan_public_ip" if role == "plan-web" else "game_public_ip"]
            request = urllib.request.Request(
                "https://" + host + path, headers={"Authorization": "Basic " + auth}
            )
            with urllib.request.urlopen(request, context=context, timeout=30) as response:
                if response.status != 200:
                    raise ValueError("Public authenticated web acceptance failed")
                if path != "/":
                    json.load(response)
        write(
            output / (role + "-public-acceptance.json"),
            {"status": "AUTHENTICATED_PUBLIC_ACCEPTED", "paths": paths, "tls_verified": True},
        )
