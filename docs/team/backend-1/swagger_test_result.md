# Backend A Swagger 시험 결과

- 시험일: 2026-10-07
- 브랜치: `idh`
- 실행 주소: `http://127.0.0.1:18001`
- Swagger UI: `/docs`
- OpenAPI Schema: `/openapi.json`
- 저장소: 인메모리 Repository
- Workflow: MockEngine

## Swagger 및 OpenAPI

| 항목 | 결과 |
|---|---|
| `GET /docs` | 200 PASS |
| `GET /openapi.json` | 200 PASS |
| OpenAPI title | `Game Event AutoHealing Backend` |

OpenAPI에 등록된 경로:

- `POST /api/events/generate`
- `POST /api/test/events/generate`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/trace`
- `GET /api/tasks/{task_id}/tool-logs`
- `POST /api/tasks/{task_id}/approval`
- `GET /health/live`
- `GET /health/ready`

## 정상 흐름

| 시험 | 결과 |
|---|---|
| 이벤트 생성 | 201, `WAITING_APPROVAL` |
| Task 재조회 | 생성한 `task_id`와 일치 |
| Trace 조회 | 2건 반환 |
| Tool Log 조회 | B 미연결 상태이므로 `[]` |
| Task 목록 | 생성한 Task 포함 |
| REQUEST_REVISION | 결과 버전 1 → 2 |
| 수정 후 상태 | `WAITING_APPROVAL` |
| APPROVE | `COMPLETED` |
| 최종 결과 | `final_result` 존재 |
| Live | `ok` |
| Ready | `ready` |

정상 흐름 시험 Task: `TASK-436F5E2B93E9`

## 오류·안전 계약

| 시험 | HTTP | Error Code / 결과 |
|---|---:|---|
| 기간 8일 요청 | 422 | `INPUT_VALIDATION_ERROR` |
| 동일 생성 요청 재전송 | 200 | 기존 Task 재사용 |
| 동일 Key + 다른 본문 | 409 | `IDEMPOTENCY_KEY_CONFLICT` |
| 오래된 결과 버전 승인 | 409 | `STALE_RESULT_VERSION` |
| Test Mode 비활성 상태 호출 | 403 | `FAULT_INJECTION_DISABLED` |

## 판정

Backend A의 Swagger/OpenAPI 등록, MockEngine 기반 생성·조회·수정·승인 흐름, 공통 오류와
멱등성 계약이 정상 동작한다.

이 시험은 실제 AI Agent, MCP Tool, 게임 규칙 검증, AutoHealing 동작을 증명하지 않는다.
해당 기능은 Backend B 및 MCP 연동 후 별도 통합 시험이 필요하다.

시험 종료 후 Uvicorn 서버를 정상 종료했다.
