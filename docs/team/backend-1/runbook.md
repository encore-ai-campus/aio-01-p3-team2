# Backend A 실행 및 인수인계

## 현재 실행 수준

Backend A는 생성 → 조회 → 승인/수정/거절을 실행할 수 있다. `backend-test`에서는
Backend B Engine도 연결되어 있으며 Mock LLM·MCP 또는 실제 MCP 전송을 선택할 수 있다.
실제 OpenAI 모델을 통한 기획·검수 품질은 아직 확인하지 않았다.

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
`/health/ready`는 Repository와, 실제 MCP 모드일 때 `get_game_rules(HUNT_001)` 조회를
확인한다. OpenAI API 호출로 준비 상태를 검사하지 않는다. 예시 `.env` 파일은 자동으로
로드되지 않으므로 실제 실행 환경에 환경변수를 설정해야 한다.

로컬 실제 MCP 연결 예시:

```powershell
$env:WORKFLOW_ENGINE="backend_b_mcp"
$env:MCP_URL="http://127.0.0.1:8010/mcp"
python -m uvicorn backend.app.main:app --reload
```

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
