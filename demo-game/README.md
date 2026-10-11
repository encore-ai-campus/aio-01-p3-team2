# 오퐁 히어로즈 가상 게임

Phaser 3와 Vite로 실행하는 로컬 방치형 RPG 시연 게임이다. 승인된 이벤트 기획안을 PostgreSQL Runtime에 등록하고, 스테이지·성장·던전·이벤트 미션 진행을 Backend API로 판정·저장한다. 화면의 게임 장면은 Phaser가 그리고 이벤트·성장·상점 정보는 HTML/CSS UI로 표시한다.

## 로컬 실행

PostgreSQL을 시작하고 저장소 루트에서 게임 Runtime 마이그레이션과 초기 카탈로그/가상 유저를 적용한다. `seed_game_runtime`은 반복 실행할 수 있으며 진행 중인 게임 데이터를 초기화하지 않는다.

```powershell
$env:DATABASE_URL = "postgresql+psycopg://eventquest:eventquest-local-only@127.0.0.1:5433/eventquest"
backend\.venv\Scripts\python.exe -m backend.scripts.seed_game_runtime
```

Backend 터미널:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://eventquest:eventquest-local-only@127.0.0.1:5433/eventquest"
$env:WORKFLOW_ENGINE = "backend_b_mock"
$env:CANONICAL_PLANNING_MODE = "deterministic"
$env:APPROVED_ACTOR_IDS = "liveops01"
backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
```

게임 UI 터미널:

```powershell
$env:BACKEND_BASE_URL = "http://127.0.0.1:8001"
Set-Location demo-game
pnpm install
pnpm dev
```

브라우저에서 `http://127.0.0.1:5173`을 연다. Vite 개발 서버는 `/game-api/*` 요청을 Backend의 `/api/game/*`로 전달한다. 다른 Backend 주소를 사용할 때 `BACKEND_BASE_URL`을 맞춘다.

## 이벤트 적용과 플레이

1. 운영 콘솔에서 이벤트 기획안을 제출하고 검증을 마친 뒤 운영자 승인을 완료한다.
2. 게임 화면의 `승인된 Task ID` 입력란에 Task ID를 입력하고 `이벤트 적용`을 누른다.
3. 승인된 이벤트가 열어 준 월광 균열에서 미션과 보상을 확인한다. 미승인·검증 실패·지원 불가 기획은 Runtime 등록이 거부된다.
4. 스테이지를 push/farm 모드로 진행하고 성장 화면에서 골드를 사용해 강화한다. 일반 던전과 적용된 이벤트 던전은 각각 독립된 콘텐츠다.
5. 보관함과 운영 기록에서 스킨·재화·행동·이벤트 보상 상태를 확인한다.

게임 행동, 이벤트 적용, 진행도, 성장, 인벤토리와 보상 원장은 PostgreSQL에 저장된다. 새로고침이나 Backend 재시작 후에도 복원된다. 게임 Runtime 초기화는 진행 데이터를 삭제하므로 필요한 경우에만 실행한다.

## 빌드와 확인

```powershell
pnpm build
```

프록시 서버 테스트는 저장소 루트에서 실행한다.

```powershell
backend\.venv\Scripts\python.exe -m pytest demo-game/tests -q
```

빌드 파일을 Python 서버에서 제공할 때는 `pnpm build` 후 저장소 루트에서 다음을 실행한다.

```powershell
$env:BACKEND_BASE_URL = "http://127.0.0.1:8001"
backend\.venv\Scripts\python.exe demo-game/server.py
```

이 경우 게임 UI는 `http://127.0.0.1:8090`에서 열린다. 기본 개발 방식은 Vite의 `5173` 포트다.
