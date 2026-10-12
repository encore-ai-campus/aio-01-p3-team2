"""Explicit cloud service configuration; validation performs no provider calls."""

import ipaddress
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from mcp.server.transport_security import TransportSecuritySettings

ROLES = {"main-api", "fault-api", "main-mcp", "fault-mcp"}
JOURNAL_PATH = "/var/lib/eventquest/fault-lab"
JOURNAL_DIR = Path(JOURNAL_PATH)


def private_host(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        return address.is_private and not (
            address.is_loopback or address.is_unspecified or address.is_reserved
        )
    except ValueError:
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,240}\.internal", value))


def mcp_transport_security() -> TransportSecuritySettings | None:
    if os.getenv("DEPLOYMENT_MODE", "local") == "local":
        return None
    hosts = os.getenv("MCP_ALLOWED_HOSTS", "").split(",")
    origins = os.getenv("MCP_ALLOWED_ORIGINS", "").split(",")
    if not hosts or any(
        not host.endswith(":8010") or not private_host(host[:-5]) for host in hosts
    ):
        raise ValueError("MCP_ALLOWED_HOSTS requires exact private host:8010 entries")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or not private_host(parsed.hostname or "")
            or parsed.port != 8000
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
        ):
            raise ValueError("MCP_ALLOWED_ORIGINS requires exact private API origins")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def validate_production_environment(*, check_storage: bool = True) -> str:
    if os.getenv("DEPLOYMENT_MODE") != "cloud":
        raise ValueError("Production service entry requires DEPLOYMENT_MODE=cloud")
    role = os.getenv("SERVICE_ROLE", "")
    if role not in ROLES:
        raise ValueError("Production service role is invalid")
    forbidden = (
        "MIGRATION_DATABASE_URL",
        "RDS_BOOTSTRAP_DATABASE_URL",
        "RDS_BOOTSTRAP_USER",
        "DATABASE_ROLE_PASSWORDS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "OPENAI_BASE_URL",
    )
    if any(os.getenv(name) for name in forbidden):
        raise ValueError("Owner, AWS or provider override credentials are forbidden in services")
    if role.endswith("-mcp"):
        if any(
            os.getenv(name)
            for name in (
                "DATABASE_URL",
                "POSTGRES_TEST_URL",
                "OPENAI_API_KEY",
                "LLM_RECORD_ENCRYPTION_KEY",
            )
        ):
            raise ValueError("MCP service received API credentials")
        if os.getenv("MCP_HOST") != "0.0.0.0" or os.getenv("MCP_PORT") != "8010":
            raise ValueError("Production MCP bind must be 0.0.0.0:8010")
        mcp_transport_security()
        return role
    if os.getenv("MCP_DATABASE_URL"):
        raise ValueError("API service received MCP reader credentials")
    from backend.app.guards.cloud_access import CloudAccess
    from backend.app.services.settings import configured_operator_actor_id

    CloudAccess.from_environment()
    if not os.getenv("OPERATOR_ACTOR_ID") or not os.getenv("APPROVED_ACTOR_IDS"):
        raise ValueError("Production operator identity must be explicit")
    configured_operator_actor_id()
    try:
        Fernet(os.getenv("LLM_RECORD_ENCRYPTION_KEY", "").encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Production raw-record encryption key is required") from exc
    endpoint = urlsplit(os.getenv("MCP_URL", ""))
    if (
        endpoint.scheme != "http"
        or not private_host(endpoint.hostname or "")
        or endpoint.port != 8010
        or endpoint.path != "/mcp"
        or endpoint.username is not None
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("Production MCP_URL must be an explicit private /mcp endpoint")
    fault = role == "fault-api"
    if fault:
        required = {
            "CANONICAL_PLANNING_MODE": "deterministic",
            "WORKFLOW_ENGINE": "agent_mcp",
            "ENABLE_FAULT_INJECTION": "true",
            "FAULT_LAB_MODE": "isolated",
        }
        if os.getenv("OPENAI_API_KEY"):
            raise ValueError("Fault API cannot receive an OpenAI key")
        if os.getenv("FAULT_LAB_JOURNAL_DIR") != JOURNAL_PATH:
            raise ValueError("Fault journal must use the fixed persistent volume path")
        if check_storage and (not JOURNAL_DIR.is_dir() or not os.access(JOURNAL_DIR, os.W_OK)):
            raise ValueError("Fault journal volume is missing or not writable")
    else:
        required = {"CANONICAL_PLANNING_MODE": "live", "WORKFLOW_ENGINE": "agent_live"}
        if os.getenv("ENABLE_FAULT_INJECTION", "false") != "false" or os.getenv(
            "POSTGRES_TEST_URL"
        ):
            raise ValueError("Main API cannot enable fault injection or use a test URL")
        if not os.getenv("OPENAI_API_KEY", "").strip() or not os.getenv("OPENAI_MODEL", "").strip():
            raise ValueError("Main API requires explicit OpenAI model and key")
        for name in ("LLM_MAX_INPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS"):
            if not os.getenv(name, "").isdigit() or int(os.environ[name]) < 1:
                raise ValueError("Production LLM token limits must be positive integers")
        for name in ("LLM_MAX_COST_USD", "LLM_INPUT_USD_PER_MILLION", "LLM_OUTPUT_USD_PER_MILLION"):
            try:
                value = Decimal(os.getenv(name, ""))
            except InvalidOperation as exc:
                raise ValueError("Production LLM price/cost limits are required") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError("Production LLM price/cost limits must be positive")
        if os.getenv("LLM_COST_CURRENCY") != "USD" or not os.getenv("LLM_PRICING_VERSION"):
            raise ValueError("Production LLM pricing version and USD currency are required")
    if any(os.getenv(name) != value for name, value in required.items()):
        raise ValueError("Production provider/isolation modes do not match the service role")
    return role
