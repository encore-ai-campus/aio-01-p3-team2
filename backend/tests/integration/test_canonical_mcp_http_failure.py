"""Canonical MCP HTTP failures are retried, safe-stopped, and persisted."""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from backend.app.main import app
from backend.app.services import canonical_planning
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def _brief() -> dict[str, object]:
    action_id = str(uuid4())
    reward_id = str(uuid4())
    return {
        "objective": "신규 유저의 첫 스테이지 참여를 높인다",
        "target_segments": ["new_user"],
        "event_type": "growth_progression",
        "operation_mode": "finite",
        "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
        "actions": [
            {
                "action_id": action_id,
                "action_type": "stage_clear",
                "content_scope": {"content_ids": ["stage_01"]},
                "goal": {
                    "metric": "count",
                    "aggregation": "count",
                    "operator": "gte",
                    "target": 3,
                },
            }
        ],
        "rewards": [{"reward_id": reward_id, "item_id": "GOLD", "quantity": 20}],
        "mission_reward_links": [{"action_id": action_id, "reward_id": reward_id}],
        "impact_targets": [
            {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
        ],
        "source_game_version": "opong-data-1.3.0",
    }


def test_http_mcp_failure_retries_then_persists_safe_stop_three_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableMCPHandler(BaseHTTPRequestHandler):
        post_count = 0

        def do_POST(self) -> None:
            type(self).post_count += 1
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"temporary MCP outage"}')

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), UnavailableMCPHandler)
    server_thread = Thread(target=http_server.serve_forever, daemon=True)
    server_thread.start()

    async def skip_backoff(_retry_count: int) -> None:
        return None

    monkeypatch.setenv("DATABASE_URL", os.environ["POSTGRES_TEST_URL"])
    monkeypatch.setenv("MCP_URL", f"http://127.0.0.1:{http_server.server_port}/mcp")
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    monkeypatch.setattr(canonical_planning, "wait_before_retry", skip_backoff)

    results: list[dict[str, object]] = []
    try:
        with TestClient(app) as client:
            for _ in range(3):
                response = client.post(
                    "/api/v1/event-briefs",
                    json=_brief(),
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert response.status_code == 201, response.text
                results.append(response.json())

            for result in results:
                assert result["workflow_status"] == "SAFE_STOP"
                assert result["execution_profile"] == "mcp_runtime_safe_stop"
                assert result["error"]["code"] == "ERR-DEP-MCP-001"
                recovery = [
                    row for row in result["trace"] if row["event"] == "mcp.recovery_decided"
                ]
                assert [row["status"] for row in recovery] == ["RETRY"] * 3 + ["SAFE_STOP"]
                assert result["trace"][-1]["event"] == "workflow.safe_stopped"
                persisted = client.get(f"/api/v1/workflows/{result['workflow_id']}").json()
                assert persisted["workflow_status"] == "SAFE_STOP"
                assert persisted["trace"] == result["trace"]

        assert UnavailableMCPHandler.post_count == 12
    finally:
        http_server.shutdown()
        http_server.server_close()
        server_thread.join(timeout=2)
