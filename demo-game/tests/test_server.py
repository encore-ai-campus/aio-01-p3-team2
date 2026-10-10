"""Contract checks for the standalone local game API proxy."""

from __future__ import annotations

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


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class BackendHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/api/game/missing":
                    self.send_response(404)
                    body = b'{"error_code":"event_not_found"}'
                else:
                    self.send_response(200)
                    body = b'{"user":{"level":2}}'
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                received = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(received)))
                self.end_headers()
                self.wfile.write(received)

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

    def test_proxy_forwards_api_get_post_and_backend_errors(self) -> None:
        base = f"http://127.0.0.1:{self.game_server.server_port}"
        backend_url = f"http://127.0.0.1:{self.backend.server_port}"
        with patch.dict(os.environ, {"BACKEND_BASE_URL": backend_url}):
            with request.urlopen(f"{base}/game-api/state") as response:
                self.assertEqual(json.load(response), {"user": {"level": 2}})
            payload = json.dumps({"request_id": "test"}).encode()
            forwarded = request.Request(
                f"{base}/game-api/events/apply",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(forwarded) as response:
                self.assertEqual(response.read(), payload)
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(f"{base}/game-api/missing")
            self.assertEqual(caught.exception.code, 404)
            self.assertEqual(json.load(caught.exception), {"error_code": "event_not_found"})

    def test_server_serves_game_entrypoint_and_rejects_non_api_post(self) -> None:
        base = f"http://127.0.0.1:{self.game_server.server_port}"
        with request.urlopen(base) as response:
            page = response.read().decode("utf-8")
        self.assertIn("오퐁 히어로즈", page)
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(request.Request(f"{base}/not-an-api", data=b"{}", method="POST"))
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
