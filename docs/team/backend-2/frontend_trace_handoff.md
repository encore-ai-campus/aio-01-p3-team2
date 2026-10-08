# 프론트엔드 Trace 요청에 대한 Backend B 구현 결과

기준: 2026-10-08 `backend-test` 작업 트리. 아래 순서는 **B Engine의 Mock 시험과 A Loader→실제 MCP 서버→B Engine 시험**으로 확인했다. 현재 API의 실제 응답 JSON 예시는 아니다.

## 확인된 B 이벤트 순서

| 사례 | B Trace의 핵심 순서 | 결과 |
| --- | --- | --- |
| 정상 | `mcp/get_event_planning_context` → `event_planner_agent/create_plan` → `mcp/get_game_rules`·`mcp/get_item_info` → `balance_rule_agent/validate_plan` → `critic_agent/final_review` | `WAITING_APPROVAL` |
| 기획 오류 | `balance_rule_agent/validate_plan ERROR` → `supervisor/RUN_AGENT` → 기획용 MCP 재조회 → `event_planner_agent/regenerate_plan` → `mcp` 검증용 재조회(`phase=revalidation`) → `balance_rule_agent/validate_plan SUCCESS` | `WAITING_APPROVAL` |
| 첫 MCP 조회 지속 실패 | `mcp/get_event_planning_context ERROR`와 `supervisor/RETRY` 반복 → `supervisor/SAFE_STOP` | `FAILED`, Planner 미실행, `current_result=null` |
| 검증용 MCP 조회 일시 실패 | `mcp/get_game_rules` 또는 `mcp/get_item_info ERROR` → `supervisor/RETRY` → 같은 `mcp` 조회 `SUCCESS` → 규칙 검증 | `WAITING_APPROVAL` |
| Primary 규칙 조회 실패 및 Fallback 성공 | `mcp/get_game_rules ERROR` → `supervisor/TOOL_FALLBACK` → `mcp/get_game_rules_fallback SUCCESS` → 규칙 검증 | `WAITING_APPROVAL`, `data_source=fallback_snapshot` |

정상 실행의 기획용 조회에는 `phase=planning_context`, 선택 ID 조회에는 `phase=validation`, 수정 후 조회에는 `phase=revalidation`을 B 이벤트에 기록한다. 성공한 MCP 조회에는 확인된 `tool_name`, `game_data_version`, `data_source`를 기록한다. B 이벤트의 `time`은 UTC 오프셋이 있는 ISO 형식이며 `reason`은 짧은 실행·실패 사유다. Task별 `sequence`와 저장된 `result_version`의 확정은 A 책임이다.

검증용 조회 실패 Trace는 확인되지 않은 `game_data_version`과 `data_source`를 채우지 않는다. Planner 지시는 `planning_context`가 전달된 경우 허용된 콘텐츠·보상 ID와 일자별 예상 레벨을 기준으로 초안을 만들도록 명시한다. Planner의 직접 Tool 호출 권한은 없다.

## API에서 아직 확인할 수 없는 부분

- 최신 A `PlanningContextLoader.load(target_user, duration)`는 기획용 Tool 호출과 기본 응답 검사를 제공한다. B Engine에 주입한 Mock 통합 시험에서는 기획 전 조회와 상세 검증이 이어졌다. 다만 A `BackendBWorkflowAdapter`는 Loader를 생성만 하고 Engine에 주입하지 않아 API 정상 경로는 여전히 Planner 먼저 실행된다.
- 새 MCP Tool은 `port` 병합 후 실제 HTTP 호출과 A Loader→B Engine 전체 실행을 통과했다. 기획 전 조회, Planner, 선택 ID별 MCP 재조회, 규칙 검증, Critic 순서를 확인했다. 다만 A Adapter에서 B Engine으로 Loader를 전달하지 않아 API 기획 전 조회는 아직 실행되지 않는다. 이번 범위에는 기획용 대체 Tool이 없으며 조회 실패 시 재시도 후 안전 종료한다.
- 최신 A `TraceEntry` Schema 및 Adapter는 B의 `phase`와 `tool_name`을 보존한다. 프론트엔드가 요청한 새 순서의 실제 `{"task_id":"...","traces":[...]}` 응답 JSON 3종은 새 MCP Tool과 Loader 연동 후 다시 수집해야 한다.
- `sequence`, `time`, `actor`, `action`, `status`, `reason`, `error_type`, `data_source`, `game_data_version`은 현재 API Trace Schema에 있다. 프론트엔드가 이미 사용하는 API 필드 이름은 유지하는 방향으로 A와 합의하면 된다.

이 문서의 장애·수정 순서는 `backend/tests/test_workflow.py`의 B Mock 시험으로 확인했다. 정상 순서는 `backend/tests/test_backend_b_mcp_live.py`에서 실제 MCP HTTP 서버를 통한 B Engine 실행으로도 확인했다. A Loader가 기본 응답을 허용해도 상세 자료가 빠지면 B가 Planner 실행 전에 Safe Stop하며, 보상 아이템의 데이터 버전 누락도 승인 후보 없이 종료한다. A API·프론트엔드 화면을 거친 새 순서의 통합 검증 결과로 표시하지 않는다.

## 2026-10-08 실제 API 통합 검증 결과

A 커밋 `2b4e581`에서 Adapter가 Loader를 B Engine에 주입했다. 위 "API에서 아직 확인할 수 없는 부분"은 그 전의 기록이다. 병합 후 MCP 서버와 Backend를 실제로 실행하고 HTTP로 `POST /api/events/generate`·`POST /api/test/events/generate`와 `GET /api/tasks/{task_id}/trace`를 호출해 아래를 확인했다.

- 실행 구성: MCP 서버는 JSON 기본 데이터(`MCP_DATABASE_URL` 미설정), Backend는 인메모리 저장소(`DATABASE_URL` 미설정)와 `ENABLE_FAULT_INJECTION=true`. `backend_b_mcp`는 Mock LLM과 실제 MCP, `backend_b_live`는 실제 OpenAI(`gpt-4.1-mini`)와 실제 MCP를 사용한다.
- 공용 PostgreSQL을 거친 결과가 아니다. 실제 LLM 사례는 각 1회 실행이다.

| 사례 | 모드 | 결과 | 원본 Trace |
| --- | --- | --- | --- |
| 정상 | `backend_b_mcp` | `WAITING_APPROVAL`, 15행 | `trace_samples/normal_mock_llm.json` |
| 정상 | `backend_b_live` | `WAITING_APPROVAL`, 19행 | `trace_samples/normal_live_llm.json` |
| 기획 오류 주입(`mission_unreachable_once`) | `backend_b_mcp` | `WAITING_APPROVAL`, `revision_count=1`, 26행 | `trace_samples/planning_error_mock_llm.json` |
| 기획 오류 주입(`mission_unreachable_once`) | `backend_b_live` | `WAITING_APPROVAL`, `revision_count=2`, 43행 | `trace_samples/planning_error_live_llm.json` |
| 첫 기획용 조회 지속 실패(MCP 서버 중지) | `backend_b_live` | `FAILED`, `mcp_timeout`, `retry_count=3`, 10행 | `trace_samples/planning_lookup_failure.json` |
| 검증용 조회 일시 실패(`mcp_timeout_once`) | `backend_b_mcp` | `WAITING_APPROVAL`, 17행 | `trace_samples/validation_lookup_retry.json` |
| Primary 실패 후 Fallback(`primary_tool_error_once`) | `backend_b_mcp` | `WAITING_APPROVAL`, `data_source=fallback_snapshot`, 17행 | `trace_samples/primary_fallback.json` |

`trace_samples/`의 파일은 `GET /api/tasks/{task_id}/trace`의 실제 응답 전체(`{"task_id": ..., "traces": [...]}`)다. 발췌나 수정 없이 저장했다.

확인된 순서는 다음과 같다.

- 정상: `backend/create_task` → `mcp/get_event_planning_context`(`planning_context`) → `event_planner_agent/create_plan` → `balance_rule_agent/HANDOFF_ACCEPTED` → `mcp/get_game_rules`·`mcp/get_item_info`(`validation`, 미션·보상 수만큼) → `balance_rule_agent/validate_plan` → `critic_agent/HANDOFF_ACCEPTED` → `critic_agent/final_review` → `critic_agent/WAITING_APPROVAL` → `backend/candidate_saved`.
- 기획 오류: `balance_rule_agent/validate_plan ERROR`(`mission_unreachable`) → `supervisor/RUN_AGENT` → `mcp/get_event_planning_context` 재조회 → `event_planner_agent/regenerate_plan` → `mcp` 조회(`revalidation`) → `balance_rule_agent/validate_plan SUCCESS`.
- 첫 조회 지속 실패: `mcp/get_event_planning_context ERROR`와 `supervisor/RETRY`가 3회 반복된 뒤 네 번째 `ERROR` → `supervisor/SAFE_STOP` → `backend/safe_stop FAILED`. Planner는 실행되지 않고 `data_source`·`game_data_version`은 `null`이다.
- 검증용 조회 일시 실패: `mcp/get_game_rules ERROR`(`mcp_timeout`) → `supervisor/RETRY` → `mcp/get_game_rules SUCCESS`.
- Fallback: `mcp/get_game_rules ERROR`(`mcp_tool_error`) → `supervisor/TOOL_FALLBACK` → `mcp/get_game_rules_fallback SUCCESS`(`fallback_snapshot`).

화면 구현 시 유의할 점이다.

- 미션·보상 항목마다 MCP 조회 행이 하나씩 생긴다. 5일 이벤트는 검증 조회만 8~10행이므로 행 수를 고정값으로 가정하지 않는다.
- 실제 LLM은 기획 수정에 두 번이 걸릴 수 있다. 실제 LLM의 기획 오류 사례에서는 첫 수정안도 `mission_unreachable`로 다시 실패해 `validate_plan ERROR` → `RUN_AGENT` → 재조회 → `regenerate_plan` 묶음이 두 번 나왔다. 자동 수정 한도는 2회이며 세 번째 실패는 `FAILED`로 끝난다.
- 실제 LLM을 쓰는 생성 요청은 응답까지 정상 사례 약 10초, 수정 2회 사례 약 23초가 걸렸다. Mock LLM은 1초 미만이다.
