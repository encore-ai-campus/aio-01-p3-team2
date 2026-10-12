"""Host-only parameter loading. Values are never printed or passed on command lines."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from infra.aws.iac import PARAMETERS, PREFIX, ROLES
from infra.aws.infrastructure import Aws

SECRET_ENV = {
    "plan-web": {
        "WEB_PASSWORD_HASH": "plan/basic-auth-hash",
        "MAIN_PROXY_KEY": "proof/plan-main",
        "FAULT_PROXY_KEY": "proof/plan-fault",
    },
    "game-web": {"WEB_PASSWORD_HASH": "game/basic-auth-hash", "MAIN_PROXY_KEY": "proof/game-main"},
    "main-api": {
        "OPENAI_API_KEY": "shared/openai-api-key",
        "DATABASE_URL": "main/runtime-database-url",
        "LLM_RECORD_ENCRYPTION_KEY": "main/llm-record-key",
    },
    "fault-api": {
        "DATABASE_URL": "fault/runtime-database-url",
        "LLM_RECORD_ENCRYPTION_KEY": "fault/llm-record-key",
    },
    "main-mcp": {"MCP_DATABASE_URL": "main/mcp-database-url"},
    "fault-mcp": {"MCP_DATABASE_URL": "fault/mcp-database-url"},
}
FORBIDDEN = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "MIGRATION_DATABASE_URL",
    "RDS_BOOTSTRAP_DATABASE_URL",
    "RDS_BOOTSTRAP_USER",
    "DATABASE_ROLE_PASSWORDS",
    "OPENAI_BASE_URL",
}


def render(role, config, values):
    if role not in ROLES or set(values) != set(PARAMETERS[role]):
        raise ValueError("Parameter scope does not match the host role")
    environment = dict(config["environment"])
    reserved = FORBIDDEN | {"CLOUD_PROXY_IDENTITIES", "POSTGRES_TEST_URL"}
    reserved |= {name for mapping in SECRET_ENV.values() for name in mapping}
    reserved |= {"DEPLOYMENT_MODE", "SERVICE_ROLE", "DEPLOYMENT_ENVIRONMENT_ID"}
    if reserved & environment.keys():
        raise ValueError("Host config cannot contain credential or identity overrides")
    environment.update(
        DEPLOYMENT_MODE="cloud", SERVICE_ROLE=role, DEPLOYMENT_ENVIRONMENT_ID="ikonly-demo"
    )
    for name, suffix in SECRET_ENV[role].items():
        environment[name] = values[suffix]
    if role.endswith("api"):
        permitted = (
            {"plan-web": "proof/plan-fault"}
            if role == "fault-api"
            else {"plan-web": "proof/plan-main", "game-web": "proof/game-main"}
        )
        if set(config.get("proxy_peers", {})) != set(permitted):
            raise ValueError("API proxy peers do not match allowed sites")
        environment["CLOUD_PROXY_IDENTITIES"] = json.dumps(
            {
                site: {"peers": config["proxy_peers"][site], "secret": values[suffix]}
                for site, suffix in permitted.items()
            },
            separators=(",", ":"),
        )
        if role == "fault-api":
            environment["POSTGRES_TEST_URL"] = environment["DATABASE_URL"]
    lines = []
    for name, value in sorted(environment.items()):
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
            or not isinstance(value, str)
            or not value
            or any(char in value for char in "\r\n\0")
        ):
            raise ValueError("Environment values must be nonempty single-line strings")
        # Compose env_file preserves single quoted JSON, bcrypt '$', URI and key values.
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(name + "='" + escaped + "'")
    return "\n".join(lines) + "\n"


def atomic_write(path, text):
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("Runtime environment files must be written by root on the Linux host")
    if path.parent.resolve() != Path("/etc/eventquest") or path.name != "runtime.env":
        raise ValueError("Runtime output must be /etc/eventquest/runtime.env")
    fd, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    role = Path("/etc/eventquest/service-role").read_text().strip()
    if role not in ROLES:
        raise ValueError("Unknown host role")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if set(config) - {"environment", "proxy_peers"}:
        raise ValueError("Unknown host configuration field")
    aws = Aws("ap-northeast-2")
    response = aws(
        "ssm",
        "get-parameters",
        payload={"Names": [PREFIX + suffix for suffix in PARAMETERS[role]], "WithDecryption": True},
    )
    if response.get("InvalidParameters"):
        raise ValueError("Required parameters are missing")
    if any(item["Type"] != "SecureString" for item in response["Parameters"]):
        raise ValueError("Sensitive configuration must be SecureString")
    values = {item["Name"].removeprefix(PREFIX): item["Value"] for item in response["Parameters"]}
    atomic_write(Path("/etc/eventquest/runtime.env"), render(role, config, values))
    print("Role-scoped runtime file updated (values suppressed)")


if __name__ == "__main__":
    main()
