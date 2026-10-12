"""Local first deployment with reviewed policy and local OpenAI key supply."""

import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from backend.app.config.cloud_database import DATABASES
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from infra.aws.export_environment import export
from infra.aws.fleet_release import deploy
from infra.aws.host_release import run
from infra.aws.iac import ROLES, logical
from infra.aws.infrastructure import owned_stack, plan, review
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


def create_stack(aws, config, stage, parameters, output):
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


def initial(aws, config, directory, run_id, env_file, policy_file, approved_policy_hash, output):
    output = Path(output)
    if any(
        s["StackName"].startswith("ikonly-demo-")
        for s in aws("cloudformation", "describe-stacks")["Stacks"]
    ):
        raise ValueError("First deployment requires reset of existing owned application stacks")
    from infra.aws.cleanup import verify_reset

    if any(verify_reset(aws).values()):
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
    write(output / "initial-start.json", {"baseline_empty": True, "lock_token": lock["token"]})
    network = create_stack(
        aws, config, "network", {"OperatorCidr": config["operator_cidr"]}, output
    )
    access = create_stack(
        aws, config, "access", {"EnableGithubRoles": "false", "GithubOidcProviderArn": ""}, output
    )
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
    )
    ami = (
        config.get("ami_id")
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
    outputs = create_stack(aws, config, "application", parameters, output)
    create_stack(
        aws,
        config,
        "observability",
        {
            "NotificationEmail": config["notification_email"],
            **{logical(role) + "Instance": outputs[logical(role)] for role in ROLES},
        },
        output,
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
    )
