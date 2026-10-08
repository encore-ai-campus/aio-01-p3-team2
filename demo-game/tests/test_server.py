"""Contract checks for the standalone local demo game."""

import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

MODULE_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("demo_game_server", MODULE_PATH)
assert SPEC and SPEC.loader
game = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(game)


def task_response(*, approved: bool = True) -> dict[str, object]:
    return {
        "task_id": "TASK-123456789ABC",
        "result_version": 1,
        "workflow_status": "COMPLETED" if approved else "WAITING_APPROVAL",
        "approval_status": "APPROVED" if approved else "PENDING",
        "critic_status": "PASS",
        "rule_validation": {"is_valid": True, "game_data_version": "tinyheroes-1.0.0"},
        "final_result": {
            "event_name": "신규 유저 모험",
            "target_user": "new_user",
            "duration": 1,
            "missions": [{"day": 1, "mission_type": "monster_kill", "target_count": 3}],
            "rewards": [{"day": 1, "item_id": "ITEM_001", "quantity": 1}],
        },
    }


class ContractTests(unittest.TestCase):
    def test_only_approved_validated_final_result_is_exposed(self) -> None:
        event = game.approved_event(task_response())
        self.assertEqual(event["task_id"], "TASK-123456789ABC")
        self.assertEqual(event["missions"][0]["target_count"], 3)
        self.assertNotIn("approval_status", event)

        with self.assertRaises(PermissionError):
            game.approved_event(task_response(approved=False))
        failed = task_response()
        failed["rule_validation"]["is_valid"] = False
        with self.assertRaises(PermissionError):
            game.approved_event(failed)

    def test_clash_sample_and_demo_missions_reach_game(self) -> None:
        task = task_response()
        task["final_result"]["game_source"] = "clash_of_clans"
        task["final_result"]["sample_summary"] = {"player_count": 20, "clan_count": 3}
        task["final_result"]["missions"] = [
            {"day": 1, "content_id": "COC_RAID_WIN", "mission_type": "raid_win", "target_count": 1}
        ]
        event = game.approved_event(task)
        self.assertEqual(event["game_source"], "clash_of_clans")
        self.assertEqual(event["sample_summary"]["player_count"], 20)
        self.assertEqual(event["missions"][0]["mission_type"], "raid_win")


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class BackendHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps(task_response()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
        cls.game_server = ThreadingHTTPServer(("127.0.0.1", 0), game.Handler)
        cls.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (cls.backend, cls.game_server)
        ]
        for thread in cls.threads:
            thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        for server in (cls.backend, cls.game_server):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)

    def test_proxy_reads_approved_task_and_serves_page(self) -> None:
        base = f"http://127.0.0.1:{self.game_server.server_port}"
        backend_url = f"http://127.0.0.1:{self.backend.server_port}"
        with patch.dict(os.environ, {"BACKEND_BASE_URL": backend_url}):
            with request.urlopen(f"{base}/game-api/task/TASK-123456789ABC") as response:
                event = json.load(response)
            with request.urlopen(base) as response:
                page = response.read().decode("utf-8")
        self.assertEqual(event["event_name"], "신규 유저 모험")
        self.assertIn("Event Quest AI", page)

    def test_invalid_task_id_is_rejected_without_backend_lookup(self) -> None:
        base = f"http://127.0.0.1:{self.game_server.server_port}"
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(f"{base}/game-api/task/not-a-task")
        self.assertEqual(caught.exception.code, 400)
