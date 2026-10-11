"""Owned loopback HTTP faults; proxy real JSON MCP traffic without logging bodies."""

import socket
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import urlsplit

import httpx

MODES = {"http503_once", "http503_always", "disconnect_once", "delay_once"}


@contextmanager
def fault_proxy(upstream: str, mode: str, *, delay_seconds: float = 6):
    target = urlsplit(upstream)
    if (
        target.scheme != "http"
        or target.hostname != "127.0.0.1"
        or target.username
        or target.password
        or target.query
        or target.fragment
        or target.path != "/mcp"
        or mode not in MODES
        or not 0 < delay_seconds <= 10
    ):
        raise ValueError("LOCAL_MCP_PROXY_CONFIGURATION_REQUIRED")
    observations = []
    lock = Lock()
    post_count = 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # The real server uses JSON responses; no persistent SSE subscription.
            self.send_error(405)

        def do_POST(self):
            nonlocal post_count
            if self.path != "/mcp":
                self.send_error(404)
                return
            self.request_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with lock:
                post_count += 1
                attempt = post_count
            affected = attempt == 1 or mode == "http503_always"
            if affected and mode == "disconnect_once":
                observations.append({"attempt": attempt, "wire_fault": "disconnect"})
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if affected:
                observations.append({"attempt": attempt, "wire_fault": mode})
                if mode == "delay_once":
                    time.sleep(delay_seconds)
                try:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A timeout/disconnect is the requested wire failure.
                return
            self.forward()

        def do_DELETE(self):
            if self.path != "/mcp":
                self.send_error(404)
                return
            self.forward()

        def forward(self):
            body = (
                self.request_body
                if self.command == "POST"
                else self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in {"host", "content-length", "connection", "transfer-encoding"}
            }
            try:
                with httpx.Client(timeout=10, trust_env=False) as client:
                    result = client.request(self.command, upstream, content=body, headers=headers)
                observations.append({"method": self.command, "upstream_status": result.status_code})
                self.send_response(result.status_code)
                for k, v in result.headers.items():
                    if k.lower() not in {
                        "content-length",
                        "connection",
                        "transfer-encoding",
                        "content-encoding",
                    }:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(result.content)))
                self.end_headers()
                self.wfile.write(result.content)
            except (httpx.HTTPError, BrokenPipeError, ConnectionResetError):
                observations.append({"method": self.command, "transport_error": True})

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", observations
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
