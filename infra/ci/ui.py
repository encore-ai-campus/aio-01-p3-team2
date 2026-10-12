"""Owned loopback API, restart, HTTP failure and existing Chromium UI contracts."""

import json
import os
import subprocess
import sys
import time
import urllib.request

from infra.ci.gate import ROOT, write


def wait(port, path):
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Owned CI service readiness timeout")


def stop(process):
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main():
    processes, expected = [], []
    env = {
        **os.environ,
        "WORKFLOW_ENGINE": "backend_b_mock",
        "MCP_URL": " ",
        "APP_ALLOWED_ORIGINS": "http://127.0.0.1:5197,http://127.0.0.1:5193",
    }
    api = None
    try:
        for phase in ("connected", "restart", "game", "fault-display", "mcp503"):
            if phase != "game":
                stop(api)
                if phase == "restart":
                    env["EVENTQUEST_E2E_RESTORE_STATE"] = (
                        ROOT / "frontend-web/test-results/e2e-restore-state.json"
                    ).read_text(encoding="utf-8")
                if phase == "mcp503":
                    env["MCP_URL"] = "http://127.0.0.1:8014/mcp"
                    env["EVENTQUEST_E2E_CANONICAL_MCP_503"] = "1"
                    failure = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "backend.tests.support.mcp_503_server",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8014",
                        ],
                        cwd=ROOT,
                        env=env,
                    )
                    processes.append(failure)
                    wait(8014, "/health")
                api = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "backend.app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8015",
                    ],
                    cwd=ROOT,
                    env=env,
                )
                processes.append(api)
                wait(8015, "/health/ready")
            current = {**env, "CI_UI_PHASE": phase}
            command = [
                "node",
                "node_modules/@playwright/test/cli.js",
                "test",
                "--config=playwright.ci.config.js",
            ]
            listed = json.loads(
                subprocess.check_output(
                    command + ["--list", "--reporter=json"], cwd=ROOT / "frontend-web", env=current
                )
            )

            def titles(suite):
                for spec in suite.get("specs", []):
                    expected.append(spec["title"])
                for child in suite.get("suites", []):
                    titles(child)

            for suite in listed["suites"]:
                titles(suite)
            subprocess.run(command, cwd=ROOT / "frontend-web", env=current, check=True)
        write(ROOT / "test-results/ui-expected.json", sorted(set(expected)))
    finally:
        for process in reversed(processes):
            stop(process)


if __name__ == "__main__":
    main()
