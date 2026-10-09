# Backend A 실행 및 인수인계

## 현재 실행 수준

Backend A는 생성 → 조회 → 승인/수정/거절을 실행할 수 있다. `backend-test`에서는
Backend B Engine도 연결되어 있으며 Mock LLM·MCP 또는 실제 MCP 전송을 선택할 수 있다.
실제 OpenAI 모델을 통한 기획·규칙 추가 검토·종합 검토·경로 심사는
`backend_b_live` 모드에서 실행할 수 있다.
모델 응답 품질과 실제 서비스 연동은 실행마다 별도로 확인한다.

## 로컬 실행

```powershell
python -m pip install -r backend/requirements.txt -r requirements-dev.txt
python -m uvicorn backend.app.main:app --reload
```

- API 문서: `http://127.0.0.1:8000/docs`
- Live: `http://127.0.0.1:8000/health/live`
- Ready: `http://127.0.0.1:8000/health/ready`

`DATABASE_URL`이 비어 있으면 인메모리 Repository를 사용한다. 프로세스를 다시 시작하면 데이터가
사라지므로 실제 연동 환경에서는 PostgreSQL URL을 설정한다.

Task 목록은 생성 시각 내림차순으로 반환한다. 생성 시각이 같으면 저장소가 기록한 생성 순번의
내림차순을 적용한다. PostgreSQL 사용 시 `task_creation_order` 테이블이 자동 생성되며, 기존
`workflow_runs` 데이터에 순번이 없으면 시작할 때 생성 시각과 Task ID 순으로 순번을 채운다.
기존 데이터의 실제 생성 순서는 기록되어 있지 않아 동률 시각의 과거 Task 순서는 복원할 수 없다.

## 동시 요청 멱등성

이벤트 생성은 `event:<idempotency_key>`, 승인·수정 요청·거절은
`approval:<task_id>:<result_version>` 단위로 직렬화한다. PostgreSQL에서는
키별 advisory lock을 사용하고, Task 생성과 멱등성 키 기록 또는 승인 결과와
멱등성 키 기록을 각각 한 DB 트랜잭션으로 저장한다. 같은 키·같은 내용의
재요청은 기존 Task를 반환하고, 같은 키·다른 내용은 409로 거절한다.
같은 결과 버전에 서로 다른 운영자 결정이 동시에 들어오면 먼저 완료된 결정만
반영하고 다른 요청은 상태 충돌로 거절한다. 인메모리 저장소는 프로세스 내부
잠금만 제공하므로 여러 서버 프로세스에서 공유하지 않는다.

Task·멱등성 키의 원자적 저장과 별개로 Agent 실행·Trace 기록 전체가 하나의
트랜잭션은 아니다. 실행 도중 프로세스가 중단되면 `RUNNING` Task 복구 정책은
별도로 필요하다. 인증되지 않은 `actor_id`의 권한 문제도 이 변경으로 해결되지 않는다.

## 장애 복구 평가에서 Backend A의 기록 경계

2026-10-09 팀 합의 `workflow_evaluation_contract.md` 기준으로 복구 판정의
공식 근거는 `GET /api/tasks/{task_id}/trace`와 Task 응답이다.
`GET /api/tasks/{task_id}/tool-logs`는 보조 진단 자료이며, 비어 있어도
정상·기획 오류 후 재검증·첫 MCP 조회 실패와 안전 종료를 판정할 수 있어야 한다.
A는 B의 실행 이벤트를 Task별 `sequence`와 시간대가 있는 `time`으로 저장·조회한다.
전체 Trace 행 수는 고정하지 않고 필수 이벤트의 상대 순서를 확인한다.
관련 API 회귀 테스트는 `backend/tests/test_backend_a_planning_api.py`에 있다.

시나리오 정의, 실제 오류 관찰 여부, 복구율·복구시간 집계와 평가 보고서는
B가 담당한다. 이번 합의는 새 DB 테이블이나 API를 요구하지 않는다.
실제 누락 사례가 발견되면 A·B가 API/저장 필드 변경을 협의한다.
여러 프로세스가 같은 Task에 동시에 Trace를 기록할 때의 순번 중복 방지는
별도 운영 과제로 남아 있다.

## 환경변수

| 이름 | 용도 | 기본 동작 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL | 비어 있으면 인메모리 |
| `ENABLE_FAULT_INJECTION` | Test 생성 API 허용 | `false` |
| `APPROVED_ACTOR_IDS` | 승인 가능한 Demo ID 목록 | `liveops01` |
| `WORKFLOW_ENGINE` | `mock`, `backend_b_mock`, `backend_b_mcp`, `backend_b_live` | `mock` |
| `MCP_URL` | 실제 MCP Streamable HTTP `/mcp` 주소 | 실제 MCP 모드에서 필수 |
| `OPENAI_MODEL` | 실제 LLM 호출에 사용할 모델명 | `backend_b_live`에서 필수 |
| `OPENAI_API_KEY` | OpenAI SDK가 읽는 비밀 환경변수 | `backend_b_live`에서 필수 |

실제 비밀번호는 `.env.example`에 기록하지 않는다.

`backend_b_mcp`는 B Engine과 Mock LLM, 실제 MCP 서버를 사용한다. `backend_b_live`는
B Engine과 실제 MCP·OpenAI 전송을 사용하며 모델과 키가 없으면 시작하지 않는다.
`backend_b_live`에서는 Event Planner, Balance & Rule, Critic, Supervisor 모두 OpenAI를
호출한다. Balance & Rule의 LLM은 규칙 엔진의 실패를 통과로 바꿀 수 없고, Supervisor의
LLM은 허용된 경로를 승인하거나 안전 종료시킬 수 있다. 기술 재시도 3회와 기획 수정 2회의
상한은 코드에서 강제한다. LLM 판단 이유는 규칙 판정과 Supervisor Trace의 `reason`에 남는다.
`backend_b_mock`·`backend_b_mcp`의 네 Agent 판단은 Mock 응답이며 실제 AI 호출이 아니다.
`/health/ready`는 Repository와, 실제 MCP 모드일 때 `get_game_rules(HUNT_001)` 조회를
확인한다. OpenAI API 호출로 준비 상태를 검사하지 않는다. 일반 Uvicorn 실행은
`.env`를 자동으로 읽지 않는다. 실제 AI용 실행 명령은 아래의 `backend.app.main`을 사용한다.

실제 MCP 모드에서는 기획 전에 `get_event_planning_context`를 조회하고 Planner에 전달한다.
실행 여부는 생성된 Task의 Trace에서 해당 Tool 호출로 확인한다.

로컬 실제 MCP 연결 예시:

```powershell
$env:WORKFLOW_ENGINE="backend_b_mcp"
$env:MCP_URL="http://127.0.0.1:8010/mcp"
python -m uvicorn backend.app.main:app --reload
```

로컬 실제 AI 실행 예시(저장소 루트에서 실행):

```powershell
python -m pip install -r backend/requirements.txt
python -m mcp_server.server  # 별도 PowerShell 창에서 먼저 실행
python -m backend.app.main  # 백엔드 실행 창
```

`python -m backend.app.main`은 `backend/.env`를 읽고 `WORKFLOW_ENGINE=backend_b_live`로
시작한다. `.env`에는 `MCP_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY`가 필요하다. 이미 셸에
설정한 값은 `.env` 값보다 우선한다. `DATABASE_URL`을 비워 두면 Task는 인메모리에 저장된다.
Windows에서 Docker PostgreSQL의 공개 포트로 직접 연결할 때는 `DATABASE_URL`과 MCP 서버의
`MCP_DATABASE_URL`에 `127.0.0.1` 및 공개 포트를 설정한다. MCP 서버는 직접 실행할 때
`mcp_server/.env`를 읽는다. 두 서비스가 같은 `game_data_catalog` DB를 사용해야 한다.
실제 모델 호출에는 API 요금이 발생한다. `/health/ready`는 MCP와 저장소만 확인하므로,
새 이벤트 생성 뒤 Trace와 결과를 확인해야 OpenAI 호출 성공을 검증할 수 있다.

## 테스트

```powershell
python -m pytest backend/tests -q
python -m ruff check backend
python -m ruff format --check backend
```

실제 MCP 서버를 실행한 뒤 API 경로까지 확인하는 선택 테스트:

```powershell
$env:MCP_TEST_URL="http://127.0.0.1:8010/mcp"
python -m pytest backend/tests/test_mcp_api_integration.py -q -rs
Remove-Item Env:MCP_TEST_URL
```

실제 PostgreSQL 시험:

```powershell
$env:POSTGRES_TEST_URL="postgresql+psycopg://USER:PASSWORD@HOST:PORT/DATABASE"
python -m pytest backend/tests/test_postgres_integration.py -q
Remove-Item Env:POSTGRES_TEST_URL
```

## 공용 PostgreSQL 게임 데이터

Backend A가 `game_data_catalog` 테이블과 초기 `tiny_heroes` 데이터를 관리한다.
`backend/sql/001_game_data_catalog.sql`은 MCP 담당자의 `port` 브랜치 조회 SQL과 호환된다.
초기 데이터는 `mcp_server/data/tiny_heroes.json`을 읽어 한 행에 저장한다.
저장소 루트에서 **검토한 DB 주소**를 `DATABASE_URL`로 지정해 다음을 실행한다.

```powershell
$env:DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:PORT/DATABASE"
python -m backend.scripts.apply_game_data_catalog
```

이 명령은 기존 `tiny_heroes` 행이 초기 데이터와 같으면 아무것도 덮어쓰지 않는다.
버전이나 내용이 다르면 중단하므로 데이터 변경은 별도 검토 후 적용한다. MCP 서버는
같은 PostgreSQL DB를 `MCP_DATABASE_URL`로 조회하며, 이 스크립트는 MCP 서버의
DB 역할에 쓰기 권한을 부여하지 않는다. 공용 DB에는 담당자끼리 주소·백업·적용 시점을
확인한 뒤 실행한다. 임시 테스트 DB에서의 검증은 `POSTGRES_TEST_URL`로 선택 실행한다.

## Docker

Backend 폴더를 Build Context로 사용한다.

```powershell
docker build -t multi-agent-autohealing-backend ./backend
docker run --rm -p 8000:8000 multi-agent-autohealing-backend
```

## Backend B 연결 지점

Backend B는 `backend/app/services/workflow_service.py`의 Mock을 직접 덮어쓰기 전에 A와 다음 계약을
확인한다.

- `run(task_id, EventBrief, fault_type=None) -> WorkflowResult`
- `revise(task_id, EventBrief, approval_comment) -> WorkflowResult`
- 저장은 `WorkflowRepository`를 사용
- Tool 호출 기록은 `append_tool_log()` 사용
- B는 HTTP Response나 SQL을 직접 만들지 않음
- A는 Agent·Routing·MCP 내부 코드를 수정하지 않음

실제 B Engine 연결 시 A는 Engine Adapter 또는 의존성 주입 지점만 변경한다.

## 미완료 및 외부 의존성

- Backend B 실제 Engine 연결
- MCP 기반 게임 데이터·Tool Log·Ready 확인
- PostgreSQL Compose 통합과 운영용 Secret
- 다중 Backend Process 환경의 DB Row Lock
- 마이그레이션 도구 최종 선택

위 항목은 다른 담당자 코드나 팀 합의가 필요한 범위다. Backend A에서 완료된 것처럼 표시하지 않는다.

## 현재 확인된 완료 상태

- MockEngine 기반 생성·조회·승인·수정·거절 API
- 생성 및 승인 멱등성 기본 동작
- Task·Trace·Tool Log Repository 계약
- 실제 PostgreSQL 저장 및 Backend 컨테이너 재시작 후 Task 보존
- 공통 422·Guard·Task 없음·승인 상태 오류 응답
- Backend Docker 이미지 실행과 Live/Ready 응답

실제 Agent/MCP/AutoHealing 품질은 이 완료 상태에 포함하지 않는다.
