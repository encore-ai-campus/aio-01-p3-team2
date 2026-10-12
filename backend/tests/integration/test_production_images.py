"""Opt-in actual production images on an owned network and TLS PostgreSQL."""

import base64
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import time
from uuid import uuid4

import pytest
from backend.app.config.cloud_database import DATABASES
from backend.app.deployment.maintenance import change
from backend.tests.integration import test_cloud_database as tls_support
from cryptography.fernet import Fernet
from sqlalchemy.engine import URL

RDS_HOST = "image-test.abc.ap-northeast-2.rds.amazonaws.com"


def docker(*args, input_text=None):
    result = subprocess.run(
        ["docker", *args],
        input=input_text.encode() if input_text is not None else None,
        capture_output=True,
        timeout=40,
        check=False,
    )
    stdout, stderr = (
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )
    if result.returncode:
        # Command arguments contain only filenames/roles/public config; env files hold secrets.
        raise RuntimeError("Image verification Docker operation failed: " + stderr[-2500:])
    return (stdout + (stderr if args[0] == "logs" else "")).strip()


@pytest.fixture(scope="module")
def production_cluster(tmp_path_factory):
    yield from tls_support.tls_cluster.__wrapped__(tmp_path_factory)


def info(container):
    return json.loads(docker("inspect", container))[0]


def wait_ready(container):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        state = info(container)["State"]
        if state.get("Health", {}).get("Status") == "healthy":
            return
        if not state["Running"]:
            raise RuntimeError("Production image exited: " + docker("logs", container)[-1500:])
        time.sleep(0.5)
    raise RuntimeError("Production image readiness timed out: " + docker("logs", container)[-1500:])


def https_request(container, domain, ca_path, path, *, authorization=None, body=None, headers=None):
    port = int(info(container)["NetworkSettings"]["Ports"]["8443/tcp"][0]["HostPort"])
    context = ssl.create_default_context(cafile=str(ca_path))
    connection = http.client.HTTPSConnection(domain, port, timeout=15, context=context)
    # Connect the mapped loopback port while verifying the actual domain certificate/SNI.
    plain = socket.create_connection(("127.0.0.1", port), timeout=15)
    connection.sock = context.wrap_socket(plain, server_hostname=domain)
    request_headers = {"Host": domain, **(headers or {})}
    if authorization:
        request_headers["Authorization"] = authorization
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    try:
        connection.request(
            "POST" if body is not None else "GET", path, body=body, headers=request_headers
        )
        response = connection.getresponse()
        return response.status, response.read().decode(), dict(response.getheaders())
    finally:
        connection.close()


def test_production_image_contents_and_missing_configuration():
    if os.getenv("CLOUD_IMAGE_TESTS") != "true":
        pytest.skip("CLOUD_IMAGE_TESTS=true requires explicitly built production images")
    app_image = os.getenv("APPLICATION_TEST_IMAGE", "ikonly-application:a3")
    web_image = os.getenv("WEB_TEST_IMAGE", "ikonly-web:a3")
    for image in (app_image, web_image):
        metadata = json.loads(docker("image", "inspect", image))[0]
        assert metadata["Config"]["User"] == "10001:10001"
        assert metadata["Config"]["Healthcheck"]["Test"]
        result = subprocess.run(
            ["docker", "run", "--rm", image],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode != 0
        assert "configuration" in (result.stdout + result.stderr).lower()
    code = """from pathlib import Path
import hashlib, importlib.util
import backend.tests.support.fault_lab_runner
import mcp_server.server
root=Path('/opt/app')
assert not list(root.rglob('.env*')) and not list(root.rglob('*.key'))
assert not (root/'frontend').exists() and not (root/'docs').exists()
assert not (root/'backend/tests/conftest.py').exists()
assert not (root/'backend/.venv').exists()
assert importlib.util.find_spec('pytest') is None
assert hashlib.sha256(Path('/opt/certs/rds-ca.pem').read_bytes()).hexdigest()=='913fb5b814f17af79d4c1622584a8d0ceddf5b0d76fe353d0c7d1186cdd6b229'
print('application contents verified')
"""
    assert "verified" in docker("run", "--rm", "--entrypoint", "python", app_image, "-c", code)
    listing = docker(
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        web_image,
        "-c",
        "test -f /srv/plan/index.html && test -f /srv/game/index.html && find /srv -name '*.png' | wc -l",
    )
    assert int(listing) >= 10


def test_production_fleet_tls_routes_mcp_game_and_runner(production_cluster, tmp_path):
    tls_cluster = production_cluster
    if os.getenv("CLOUD_IMAGE_TESTS") != "true":
        pytest.skip("CLOUD_IMAGE_TESTS=true requires explicitly built production images")
    app_image = os.getenv("APPLICATION_TEST_IMAGE", "ikonly-application:a3")
    web_image = os.getenv("WEB_TEST_IMAGE", "ikonly-web:a3")
    identifier = uuid4().hex
    network = "ikonly-image-test-" + identifier
    containers, env_files = [], []
    docker("network", "create", "--label", "ikonly.image-test=" + identifier, network)
    docker("network", "connect", "--alias", RDS_HOST, network, tls_cluster["container_id"])

    def launch(role, image, settings, port):
        file = tmp_path / (role + ".env")
        file.write_text(
            "".join(f"{name}={value}\n" for name, value in settings.items()), encoding="utf-8"
        )
        env_files.append(file)
        args = [
            "run",
            "--detach",
            "--name",
            role + "-" + identifier,
            "--label",
            "ikonly.image-test=" + identifier,
            "--network",
            network,
            "--network-alias",
            role + ".internal",
            "--env-file",
            str(file),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "--memory",
            "1536m",
            "--cpus",
            "1.5",
            "--tmpfs",
            "/tmp:rw,size=64m,mode=1777",
            "--publish",
            f"127.0.0.1::{port}",
            "--health-interval",
            "2s",
            "--health-timeout",
            "10s",
            "--health-start-period",
            "5s",
        ]
        if role.endswith(("-api", "-mcp")):
            args += [
                "--mount",
                f"type=bind,source={tls_cluster['directory'] / 'ca.pem'},target=/run/db-ca.pem,readonly",
            ]
        if role == "fault-api":
            args += ["--volume", "/var/lib/eventquest/fault-lab"]
        if role.endswith("-web"):
            args += ["--volume", "/data", "--volume", "/config"]
            args += ["--publish", "127.0.0.1::8080"]
        container = docker(*args, image)
        containers.append(container)
        return container

    try:
        password = secrets.token_urlsafe(24)
        hashed = docker(
            "run",
            "--rm",
            "--interactive",
            "--entrypoint",
            "caddy",
            web_image,
            "hash-password",
            input_text=password + "\n",
        )
        authorization = "Basic " + base64.b64encode(("operator:" + password).encode()).decode()
        proofs = {name: secrets.token_urlsafe(32) for name in ("main", "fault", "game")}
        web = {}
        for role in ("plan-web", "game-web"):
            site = role.split("-")[0]
            settings = {
                "DEPLOYMENT_MODE": "verification",
                "SERVICE_ROLE": role,
                "PUBLIC_DOMAIN": site + ".localhost",
                "ACME_EMAIL": "operator@example.com",
                "WEB_USERNAME": "operator",
                "WEB_PASSWORD_HASH": hashed,
                "MAIN_API_UPSTREAM": "http://main-api.internal:8000",
                "MAIN_PROXY_KEY": proofs["main" if site == "plan" else "game"],
            }
            if site == "plan":
                settings.update(
                    FAULT_API_UPSTREAM="http://fault-api.internal:8000",
                    FAULT_PROXY_KEY=proofs["fault"],
                )
            web[role] = launch(role, web_image, settings, 8443)
            wait_ready(web[role])
        peers = {
            role: info(container)["NetworkSettings"]["Networks"][network]["IPAddress"]
            for role, container in web.items()
        }
        for role in ("main-mcp", "fault-mcp", "main-api", "fault-api"):
            purpose = role.split("-")[0]
            spec = DATABASES[purpose]
            reader = role.endswith("-mcp")
            user = spec.reader if reader else spec.runtime
            db_url = URL.create(
                "postgresql+psycopg",
                username=user,
                password=tls_cluster["passwords"][user],
                host=RDS_HOST,
                port=5432,
                database=spec.database,
                query={"sslmode": "verify-full", "sslrootcert": "/run/db-ca.pem"},
            ).render_as_string(hide_password=False)
            settings = {
                "DEPLOYMENT_MODE": "cloud",
                "SERVICE_ROLE": role,
                "DEPLOYMENT_ENVIRONMENT_ID": "cloud-test",
                "RDS_EXPECTED_ENDPOINT": RDS_HOST,
                "EXPECTED_DATABASE_ROLE": user,
                "DATABASE_AUTO_CREATE": "false",
                "MCP_DATABASE_URL" if reader else "DATABASE_URL": db_url,
            }
            if reader:
                settings.update(
                    MCP_HOST="0.0.0.0",
                    MCP_PORT="8010",
                    MCP_ALLOWED_HOSTS=f"{purpose}-mcp.internal:8010",
                    MCP_ALLOWED_ORIGINS=f"http://{purpose}-api.internal:8000",
                )
            else:
                proxies = {"plan-web": {"peers": [peers["plan-web"]], "secret": proofs[purpose]}}
                if purpose == "main":
                    proxies["game-web"] = {"peers": [peers["game-web"]], "secret": proofs["game"]}
                settings.update(
                    CLOUD_PROXY_IDENTITIES=json.dumps(proxies),
                    APP_ALLOWED_ORIGINS="https://plan.localhost,https://game.localhost",
                    OPERATOR_ACTOR_ID="liveops01",
                    APPROVED_ACTOR_IDS="liveops01",
                    MCP_URL=f"http://{purpose}-mcp.internal:8010/mcp",
                    LLM_RECORD_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                )
                if purpose == "main":
                    settings.update(
                        CANONICAL_PLANNING_MODE="live",
                        WORKFLOW_ENGINE="agent_live",
                        ENABLE_FAULT_INJECTION="false",
                        OPENAI_API_KEY="verification-no-provider-call",
                        OPENAI_MODEL="verification-model",
                        LLM_MAX_INPUT_TOKENS="10000",
                        LLM_MAX_OUTPUT_TOKENS="4000",
                        LLM_MAX_COST_USD="1",
                        LLM_INPUT_USD_PER_MILLION="1",
                        LLM_OUTPUT_USD_PER_MILLION="2",
                        LLM_COST_CURRENCY="USD",
                        LLM_PRICING_VERSION="verification-no-call",
                    )
                else:
                    settings.update(
                        CANONICAL_PLANNING_MODE="deterministic",
                        WORKFLOW_ENGINE="agent_mcp",
                        ENABLE_FAULT_INJECTION="true",
                        FAULT_LAB_MODE="isolated",
                        POSTGRES_TEST_URL=db_url,
                        FAULT_LAB_JOURNAL_DIR="/var/lib/eventquest/fault-lab",
                    )
            container = launch(role, app_image, settings, 8010 if reader else 8000)
            wait_ready(container)
            port_key = "8010/tcp" if reader else "8000/tcp"
            host_port = int(info(container)["NetworkSettings"]["Ports"][port_key][0]["HostPort"])
            if reader:
                for host, origin, expected in [
                    ("attacker.example:8010", f"http://{purpose}-api.internal:8000", 421),
                    (f"{purpose}-mcp.internal:8010", "http://attacker.example:8000", 403),
                ]:
                    connection = http.client.HTTPConnection("127.0.0.1", host_port, timeout=10)
                    try:
                        connection.request(
                            "POST",
                            "/mcp",
                            body="{}",
                            headers={
                                "Host": host,
                                "Origin": origin,
                                "Content-Type": "application/json",
                                "Accept": "application/json, text/event-stream",
                            },
                        )
                        assert connection.getresponse().status == expected
                    finally:
                        connection.close()
            else:
                connection = http.client.HTTPConnection("127.0.0.1", host_port, timeout=10)
                try:
                    connection.request(
                        "GET",
                        "/api/test/fault-status" if purpose == "fault" else "/api/game/state",
                        headers={
                            "X-Eventquest-Proxy": "plan-web",
                            "X-Eventquest-Proxy-Key": proofs[purpose],
                        },
                    )
                    assert connection.getresponse().status == 403
                finally:
                    connection.close()
        roots = {}
        for role, container in web.items():
            root = tmp_path / (role + "-root.crt")
            root.write_text(
                docker("exec", container, "cat", "/data/caddy/pki/authorities/local/root.crt"),
                encoding="ascii",
            )
            roots[role] = root
            domain = role.split("-")[0] + ".localhost"
            http_port = int(info(container)["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"])
            connection = http.client.HTTPConnection("127.0.0.1", http_port, timeout=10)
            try:
                connection.request("GET", "/", headers={"Host": domain})
                redirect = connection.getresponse()
                assert redirect.status == 308
                assert redirect.getheader("Location") == f"https://{domain}/"
            finally:
                connection.close()
            assert https_request(container, domain, root, "/")[0] == 401
            code, html, _ = https_request(container, domain, root, "/", authorization=authorization)
            assert code == 200
            asset = re.search(r'(?:src|href)="([^" ]+\.js)"', html).group(1)
            assert (
                https_request(
                    container, domain, root, "/" + asset.lstrip("./"), authorization=authorization
                )[0]
                == 200
            )

        def request(role, path, **kwargs):
            return https_request(
                web[role],
                role.split("-")[0] + ".localhost",
                roots[role],
                path,
                authorization=authorization,
                **kwargs,
            )

        assert request("plan-web", "/api/test/fault-status")[0] == 200
        assert request("game-web", "/game-api/state")[0] == 200
        assert request("game-web", "/api/v1/recovery-policies")[0] == 404
        assert (
            request(
                "plan-web",
                "/api/game/state",
                headers={"X-Eventquest-Proxy": "spoofed", "X-Eventquest-Proxy-Key": "spoofed"},
            )[0]
            == 200
        )
        # Shared deployment gate remains effective through the production HTTPS proxy.
        change(
            tls_cluster["owners"]["main"],
            environment="cloud-test",
            purpose="main",
            release_id="image-test-release",
            enabled=True,
        )
        assert (
            request(
                "game-web",
                "/game-api/growth-actions",
                body={},
                headers={"Origin": "https://game.localhost"},
            )[0]
            == 503
        )
        change(
            tls_cluster["owners"]["main"],
            environment="cloud-test",
            purpose="main",
            release_id="image-test-release",
            enabled=False,
        )
        code, body, _ = request(
            "plan-web",
            "/api/test/fault-suites",
            body={
                "cases": [
                    {"scenario_id": name, "pattern": "once", "profile": "current_active"}
                    for name in ("mcp_game_rules_error",)
                ]
            },
            headers={
                "Idempotency-Key": "image-fault-suite-" + identifier,
                "Origin": "https://plan.localhost",
            },
        )
        assert code == 202, body
        run_id = json.loads(body)["id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            code, body, _ = request("plan-web", "/api/test/fault-suites/" + run_id)
            result = json.loads(body)
            if result["lifecycle"] in {"COMPLETED", "ABORTED"}:
                break
            time.sleep(0.5)
        assert result["lifecycle"] == "COMPLETED", result
        assert result["verdict"] == "PASS", result
        fault = next(c for c in containers if info(c)["Name"].startswith("/fault-api-"))
        assert run_id in docker("exec", fault, "ls", "/var/lib/eventquest/fault-lab")
        docker("restart", fault)
        wait_ready(fault)
        assert request("plan-web", "/api/test/fault-suites/" + run_id)[0] == 200
        # Fault upstream failure cannot be served by the ordinary API.
        docker("stop", fault)
        assert request("plan-web", "/api/test/fault-status")[0] == 502
    finally:
        for container in reversed(containers):
            if info(container)["Config"]["Labels"].get("ikonly.image-test") != identifier:
                raise RuntimeError("Image container ownership mismatch; cleanup refused")
            docker("rm", "--force", "--volumes", container)
        docker("network", "disconnect", network, tls_cluster["container_id"])
        network_info = json.loads(docker("network", "inspect", network))[0]
        if network_info["Labels"].get("ikonly.image-test") != identifier:
            raise RuntimeError("Image network ownership mismatch; cleanup refused")
        docker("network", "rm", network)
        for file in env_files:
            file.unlink(missing_ok=True)
