# 백엔드 A 개발 가이드 — A/B 통합 반영

> 기준: 기존 `Backend_A_통합_개발_실행가이드.md`와 `backend-test` 구현·검증 결과(2026-10-08). 이 문서는 현재 구현과 다음 연동 작업을 구분한다. 상세 API 계약과 단계별 설계는 기존 실행 가이드를 함께 참고한다.

## 1. 담당 범위

A는 FastAPI Endpoint, 공통 Pydantic Schema와 Validation, Input/Response Guard, Repository·PostgreSQL, 승인·수정·거절, 결과 버전·멱등성, Trace 저장·조회, API 응답 변환을 담당한다. B는 Agent, Workflow Engine, Supervisor, Rule Engine, MCP Client, Tool Guard, AutoHealing 및 Fault Injection의 내부 실행을 담당한다. A는 B 내부 Agent·Tool 정책을 직접 수정하지 않는다.

현재 통합 브랜치 `backend-test`는 A의 API와 B의 Engine을 연결했으며, 원래 담당 브랜치 `idh`와 `youngmin`의 개별 작업을 대체하지 않는다. PR은 사용하지 않는다.

## 2. 현재 A/B 호출 계약

`backend/app/services/workflow_service.py`의 `BackendBWorkflowAdapter`가 A의 동기 서비스와 B의 비동기 Engine 사이를 연결한다. `WORKFLOW_ENGINE=mock`은 A의 독립 Mock Engine, `WORKFLOW_ENGINE=backend_b_mock`은 B Engine과 결정적 Mock LLM·MCP 전송을 사용한다. 실제 OpenAI·MCP 서버 연결 모드가 검증되었다는 뜻은 아니다.

| 항목 | A 책임 | B에서 받거나 호출하는 값 |
| --- | --- | --- |
| 생성 | Brief 검증, Task 저장, 멱등성·버전 저장 | `run(task_id, brief, fault_type)`의 후보 결과 |
| 수정 요청 | 승인자·상태·의견 검증, 이전 결과 보존 | `revise(task_id, approval_comment)`; Adapter의 `revision_loader`가 최신 저장 결과 제공 |
| 결과 | HTTP Schema Validation 및 화면 응답 변환 | B의 내부 dataclass/dict 결과를 A의 `WorkflowResult`, `EventResult`, `RuleValidation`으로 변환 |
| Trace | Task별 sequence·저장 버전 확정 및 Repository 저장 | B Trace를 Adapter의 `trace_sink`로 수신 |
| AutoHealing | API의 `AutoHealingEvent` 필수 필드 충족 | B의 `action/error_type/agent/tool` 등을 `sequence/error_type/reason/recovery_action/target/revalidation_status`로 변환 |

AutoHealing 변환을 빠뜨리면 `/api/test/events/generate`에서 HTTP 500이 발생했던 이력이 있다. 새 복구 액션을 추가할 때는 B 내부 이벤트와 A 응답 Schema를 함께 확인한다.

## 3. 작업 목록 생성 순서

`GET /api/tasks?limit=N`은 `created_at` 최신순이다. 시각이 같으면 **실제 저장 순번이 큰 작업을 먼저** 반환한다. `task_id`는 임의 UUID 기반이므로 정렬 기준으로 쓰지 않는다.

- 인메모리 Repository: 생성할 때 증가하는 순번을 기록하고 `(created_at, 순번)` 내림차순으로 정렬한다.
- SQLAlchemy/PostgreSQL Repository: `task_creation_order` 테이블의 자동 증가 ID를 기록하고 `created_at DESC, id DESC`로 정렬한다.
- 기존 DB의 작업에 순번이 없으면 Repository 시작 시 생성 시각·Task ID 순으로 채운다. 동률 시각의 과거 작업은 실제 생성 순서를 복원할 수 없다.
- `reset()`은 Task와 생성 순번을 함께 지운다. 실제 공유·운영 DB에서 테스트용 `reset()`을 호출하지 않는다.

동일 시각의 두 Task를 생성해 `limit=1`에 두 번째 Task가 나오는지 인메모리·SQLite·실제 PostgreSQL에서 확인한다. `backend/tests/test_workflow.py::test_task_list_is_latest_first_and_limited`와 Repository 테스트가 이 규칙을 검증한다.

## 4. 검증된 API 흐름

`WORKFLOW_ENGINE=backend_b_mock`과 `ENABLE_FAULT_INJECTION=true`에서 Swagger `/docs`와 OpenAPI `/openapi.json`이 HTTP 200이다. 생성은 `WAITING_APPROVAL`, 수정 요청은 결과 버전 2의 `WAITING_APPROVAL`, 승인은 `COMPLETED`와 `final_result`, 거절은 `CANCELLED`로 확인했다.

7개 Fault Injection의 확인 결과는 `docs/team/backend-1/backend_ab_integration_test.md`에 기록되어 있다. 복구 가능한 오류는 재실행 또는 Fallback 뒤 `WAITING_APPROVAL`, `retry_limit_exceeded`는 `FAILED`다. Primary Tool 오류는 `fallback_snapshot` 출처를 반환한다. B Engine의 내부 결과를 A 응답으로 바꾸는 Adapter를 수정하면 정상·장애·승인 시나리오를 모두 다시 확인한다.

## 5. PostgreSQL과 테스트 절차

`DATABASE_URL`이 없으면 인메모리 Repository를 사용한다. PostgreSQL 영속성 테스트는 별도의 `POSTGRES_TEST_URL`이 있을 때만 실행된다. 이 테스트는 연결 DB에서 `reset()`을 호출하므로 테스트 전용 DB만 사용한다.

```powershell
python -m pytest backend/tests -q -rs
python -m ruff check backend
python -m ruff format --check backend

$env:POSTGRES_TEST_URL="postgresql+psycopg://USER:PASSWORD@HOST:PORT/TEST_DATABASE"
python -m pytest backend/tests/test_postgres_integration.py -q -rs
Remove-Item Env:POSTGRES_TEST_URL
```

2026-10-08 확인 결과: 기본 실행 `41 passed, 2 skipped`, 테스트용 실제 PostgreSQL 실행 `2 passed`, Ruff lint·format 통과. 기본 실행의 2건 skip은 `POSTGRES_TEST_URL`이 없는 경우에 한정된다. 임시 PostgreSQL 컨테이너는 검증 후 삭제했다.

## 6. 다음 통합 작업과 완료 판단

실제 MCP 팀 서버를 연결하면 Tool 응답 Schema, Timeout, Primary 오류 시 Fallback, `data_source/game_data_version`, Trace를 재검증한다. 실제 OpenAI 연결은 모델·키·비용 정책이 확정된 뒤 별도로 검증한다. 현재 Swagger 결과는 결정적 Mock 전송 기준이다.

A/B 공통 Schema·Adapter·Repository 계약이 바뀌면 양쪽 가이드와 관련 테스트를 함께 갱신한다. 프런트엔드에는 기존 API 응답 형태를 유지하고, 내부 B 결과 형식을 그대로 노출하지 않는다.
