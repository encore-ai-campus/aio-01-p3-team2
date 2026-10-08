# Backend A 실행 및 인수인계

## 현재 실행 수준

Backend A는 Mock Workflow Engine을 사용해 생성 → 조회 → 승인/수정/거절을 실행할 수 있다.
실제 AI Agent, MCP 검증, AutoHealing은 Backend B 연결 전이므로 완료로 표시하지 않는다.

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

실제 비밀번호는 `.env.example`에 기록하지 않는다.

## 테스트

```powershell
python -m pytest backend/tests -q
python -m ruff check backend
python -m ruff format --check backend
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
