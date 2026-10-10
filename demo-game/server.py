"""Serve the local production build and proxy the virtual game API."""

from __future__ import annotations

import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
MAX_REQUEST_BYTES = 1_000_000


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        directory = DIST if DIST.is_dir() else ROOT
        super().__init__(*args, directory=str(directory), **kwargs)

    def do_GET(self) -> None:
        if parse.urlsplit(self.path).path.startswith("/game-api/"):
            self._proxy()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if parse.urlsplit(self.path).path.startswith("/game-api/"):
            self._proxy()
            return
        self._json(404, {"error_code": "not_found", "message": "요청한 경로가 없습니다."})

    def _proxy(self) -> None:
        parts = parse.urlsplit(self.path)
        route = parts.path.removeprefix("/game-api")
        if not route.startswith("/") or ".." in route.split("/"):
            self._json(
                400, {"error_code": "invalid_path", "message": "API 경로가 올바르지 않습니다."}
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(
                400, {"error_code": "invalid_request", "message": "요청 길이가 올바르지 않습니다."}
            )
            return
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._json(
                413, {"error_code": "request_too_large", "message": "요청 본문이 너무 큽니다."}
            )
            return
        body = self.rfile.read(length) if length else None
        backend_url = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        target = f"{backend_url}/api/game{route}"
        if parts.query:
            target = f"{target}?{parts.query}"
        headers = {"Accept": self.headers.get("Accept", "application/json")}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        forwarded = request.Request(target, data=body, headers=headers, method=self.command)
        try:
            with request.urlopen(forwarded, timeout=15) as response:
                status = response.status
                payload = response.read()
                response_type = response.headers.get("Content-Type", "application/json")
        except error.HTTPError as exc:
            status = exc.code
            payload = exc.read()
            response_type = exc.headers.get("Content-Type", "application/json")
        except (error.URLError, TimeoutError):
            self._json(
                502,
                {
                    "error_code": "backend_unavailable",
                    "message": "Backend 서버에 연결할 수 없습니다.",
                },
            )
            return
        self.send_response(status)
        self.send_header("Content-Type", response_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: dict[str, str]) -> None:
        import json

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.getenv("DEMO_GAME_PORT", "8090"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Virtual game: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
