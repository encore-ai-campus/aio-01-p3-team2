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
- 실제 MCP 팀 브랜치를 추가 병합한 뒤 Streamable HTTP 전송 시험이 필요하다.
- 실제 OpenAI 모델·API Key·비용 정책 확정 후 LLM 전송 시험이 필요하다.
- `youngmin` 파일 17개가 현재 저장소 Ruff format check와 줄바꿈 기준이 달라 전체
  `ruff format --check backend`는 실패한다. 기능 테스트와 `ruff check backend`는 통과한다.
