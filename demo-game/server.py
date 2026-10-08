"""Serve a tiny event-play demo and proxy one approved Backend task."""

import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent
TASK_ID = re.compile(r"TASK-[A-Z0-9]{12}\Z")


def approved_event(task: object) -> dict[str, object]:
    """Expose only the approved plan needed by the local game."""
    if not isinstance(task, dict):
        raise TypeError("백엔드 응답 형식이 올바르지 않습니다.")
    if task.get("workflow_status") != "COMPLETED" or task.get("approval_status") != "APPROVED":
        raise PermissionError("운영자 승인이 완료된 이벤트만 플레이할 수 있습니다.")
    result = task.get("final_result")
    validation = task.get("rule_validation")
    if not isinstance(result, dict) or not isinstance(validation, dict):
        raise TypeError("승인된 이벤트 결과가 없습니다.")
    if validation.get("is_valid") is not True or task.get("critic_status") != "PASS":
        raise PermissionError("규칙 검증과 AI 검수를 통과한 이벤트만 플레이할 수 있습니다.")
    missions = result.get("missions")
    rewards = result.get("rewards")
    duration = result.get("duration")
    if (
        not isinstance(missions, list)
        or not isinstance(rewards, list)
        or not isinstance(duration, int)
        or not 1 <= duration <= 7
    ):
        raise ValueError("이벤트 미션·보상 형식이 올바르지 않습니다.")
    return {
        "task_id": task["task_id"],
        "result_version": task["result_version"],
        "event_name": result.get("event_name") or "이벤트",
        "target_user": result.get("target_user"),
        "duration": duration,
        "missions": missions,
        "rewards": rewards,
        "game_data_version": validation.get("game_data_version"),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:
        path = parse.urlsplit(self.path).path
        if path == "/game-api/health":
            self._json(200, {"status": "ok"})
            return
        if path.startswith("/game-api/task/"):
            task_id = path.removeprefix("/game-api/task/")
            if not TASK_ID.fullmatch(task_id):
                self._json(400, {"message": "Task ID 형식이 올바르지 않습니다."})
                return
            backend_url = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
            target = f"{backend_url}/api/tasks/{task_id}"
            try:
                with request.urlopen(target, timeout=10) as response:
                    task = json.load(response)
                payload = approved_event(task)
            except error.HTTPError as exc:
                self._json(exc.code, {"message": "Task를 조회할 수 없습니다."})
                return
            except (error.URLError, TimeoutError):
                self._json(502, {"message": "백엔드 서버에 연결할 수 없습니다."})
                return
            except PermissionError as exc:
                self._json(409, {"message": str(exc)})
                return
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self._json(502, {"message": "백엔드 이벤트 응답을 해석할 수 없습니다."})
                return
            self._json(200, payload)
            return
        if path.startswith("/game-api/"):
            self._json(404, {"message": "지원하지 않는 경로입니다."})
            return
        super().do_GET()

    def _json(self, status: int, payload: dict[str, object]) -> None:
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
    print(f"Demo game: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
