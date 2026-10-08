# Backend A/B 통합 및 Swagger 시험

- 시험일: 2026-10-08
- 통합 브랜치: `backend-test`
- 병합 대상: `idh`, `origin/youngmin`
- Swagger 주소: `http://127.0.0.1:18002/docs`
- 실행 모드: `WORKFLOW_ENGINE=backend_b_mock`, `ENABLE_FAULT_INJECTION=true`

## 병합 결과

실제 Git 충돌은 `backend/tests/test_workflow.py` 한 파일에서 발생했다.

- 원인: 초기 빈 테스트 파일에 A와 B가 각각 독립 테스트를 작성함
- 해결: A API 테스트와 B Workflow 테스트를 모두 보존
- 결과: 39 passed, 1 skipped

## 통합 과정에서 발견한 계약 차이

### 1. 동기/비동기 Engine

- A 서비스: 동기 호출
- B Engine: async `run()` / `revise()`
- 해결: A 소유 `BackendBWorkflowAdapter`에서 async Engine 호출

### 2. Human Revision 인자

- 기존 A Mock: `revise(task_id, brief, approval_comment)`
- B Engine: `revise(task_id, approval_comment)` 후 `revision_loader` 사용
- 해결: Adapter가 A Repository에서 요청·현재 후보·수정 횟수를 공급

### 3. Workflow 결과 타입

- A: Pydantic `WorkflowResult`, `EventResult`, `RuleValidation`
- B: 내부 dataclass와 dict
- 해결: Adapter에서 검증 후 A Schema로 변환

### 4. AutoHealing Event

- A API: `sequence`, `error_type`, `reason`, `recovery_action`, `target`,
  `revalidation_status`
- B 내부: `action`, `error_type`, `agent/tool`, `retry_count/revision_count`
- 최초 Swagger 시험 결과: Pydantic Validation Error로 HTTP 500
- 해결: Adapter에서 `PLANNER_REVISION`, `RETRY`, `TOOL_FALLBACK`을 API 계약으로 변환

### 5. Trace

- B Trace는 `event_id`와 임시 `result_version=0`을 전달
- A Repository는 Task별 sequence와 저장된 result_version을 관리
- 해결: Adapter Trace Sink가 A Repository 형식으로 저장

## Swagger 결과

### 정상 실행

| 항목 | 결과 |
|---|---|
| 이벤트 생성 | `WAITING_APPROVAL` |
| 게임 데이터 출처 | `primary` |
| 게임 데이터 버전 | `tinyheroes-1.0.0` |
| Trace | 7건 |
| AutoHealing | 0건 |
| REQUEST_REVISION | result_version 2, `WAITING_APPROVAL` |

### 장애 복구

| Fault | 결과 | 세부 결과 |
|---|---|---|
| `primary_tool_error_once` | `WAITING_APPROVAL` | `TOOL_FALLBACK`, `fallback_snapshot` |
| `agent_failure_once` | `WAITING_APPROVAL` | AutoHealing 1건, retry_count 0 복귀 |
| `mcp_timeout_once` | `WAITING_APPROVAL` | AutoHealing 1건, retry_count 0 복귀 |
| `retry_limit_exceeded` | `FAILED` | retry_count 3, error_type `agent_failure` |

## 남은 사항

- 현재 통합 Swagger는 실제 OpenAI/MCP 서버가 아니라 결정적 Mock 전송을 사용한다.
- MCP 팀의 `port` 브랜치 병합 및 Streamable HTTP 직접 연결 시험 결과는 아래에 기록했다.
- 실제 OpenAI 모델·API Key·비용 정책 확정 후 LLM 전송 시험이 필요하다.
- 실제 MCP와 OpenAI 연결 전 운영 환경 Timeout과 Secret 설정을 확정해야 한다.

## 2026-10-08 수정 후 최종 재시험

통합 브랜치에서 Backend B 파일 17개의 Ruff 포맷과 줄바꿈 차이를 정리했다.

- Pytest: 39 passed, 1 skipped
- `ruff check backend`: PASS
- `ruff format --check backend`: PASS
- Swagger `/docs`: HTTP 200
- OpenAPI `/openapi.json`: HTTP 200

정상 업무 흐름:

| 시나리오 | 결과 |
|---|---|
| 이벤트 생성 | `WAITING_APPROVAL`, data_source `primary` |
| 운영자 수정 요청 | result_version 2, `WAITING_APPROVAL` |
| 승인 | `COMPLETED`, final_result 존재 |
| 거절 | `CANCELLED` |

전체 Fault Injection:

| Fault | 최종 상태 | 확인 결과 |
|---|---|---|
| `mission_unreachable_once` | `WAITING_APPROVAL` | revision_count 1, AutoHealing 1건 |
| `agent_failure_once` | `WAITING_APPROVAL` | Retry 복구 후 retry_count 0 |
| `primary_tool_error_once` | `WAITING_APPROVAL` | `fallback_snapshot` 사용 |
| `mcp_timeout_once` | `WAITING_APPROVAL` | 동일 Tool Retry 후 retry_count 0 |
| `db_connection_error_once` | `WAITING_APPROVAL` | Trace 저장 재시도 후 정상 완료 |
| `validation_error_once` | `WAITING_APPROVAL` | Output 재생성 후 정상 완료 |
| `retry_limit_exceeded` | `FAILED` | retry_count 3, `agent_failure` |

Swagger 시험 서버는 결과 확인 후 정상 종료했다.

## 2026-10-08 MCP 병합 및 직접 연결 시험

- `port` 브랜치를 `backend-test`에 병합했다. 병합 커밋: `97e89c5`.
- MCP 서버 테스트: 17 passed. 백엔드 테스트: 41 passed, 2 skipped (`POSTGRES_TEST_URL` 미설정).
- 저장소 전체 `ruff check .`, `ruff format --check .` 통과.
- 실제 MCP 서버를 로컬에서 실행하고 백엔드 `MCPClient`의 Streamable HTTP 전송으로 `get_game_rules(HUNT_001)` 기본 조회, 기본 조회 실패 후 fallback 조회, `get_item_info(ITEM_001)` 조회, 없는 콘텐츠 조회, 허용되지 않은 fallback 차단을 확인했다.
- 이 시험은 백엔드 MCP 클라이언트에서 MCP 서버까지의 직접 연결 범위다. 현재 `BackendBWorkflowAdapter`는 Mock 전송을 사용하며 기본 `WORKFLOW_ENGINE`도 `mock`이다. Frontend → Backend API → MCP 서버 전체 흐름과 실제 OpenAI 호출은 아직 검증하지 않았다.
- 실제 API Workflow 연결 전에는 백엔드 런타임 의존성에 MCP SDK를 추가하고 전송 설정을 연결해야 한다. 이번 직접 연결 시험에서는 로컬 백엔드 가상환경에 MCP SDK를 설치해 사용했다.
