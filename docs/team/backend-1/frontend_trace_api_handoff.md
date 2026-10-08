# 프론트엔드 전달: A API Trace 통합 검증

`feature/demo-game`에는 B의 Supervisor 경로 Trace와 A의 `TraceEntry`/Adapter 대응이 모두 포함돼 있다. 아래는 `POST /api/events/generate` 또는 테스트 전용 `POST /api/test/events/generate` 실행 후 `GET /api/tasks/{task_id}/trace` 응답에서 확인한 순서다. API 응답 자체는 `{"task_id":"TASK-...","traces":[...]}`이고, 각 행에는 `sequence`, 시간대가 포함된 `time`, `actor`, `action`, `status`, `reason`, `error_type`, `phase`, `next_agent`, `tool_name`, `data_source`, `game_data_version`가 있다. `task_id`와 `time`은 실행마다 바뀐다. `supervisor`의 단계 선택은 `phase=routing`, 복구 결정은 `phase=recovery`다. 다음 실행 Agent가 정해졌으면 `next_agent`에 기록한다.

| 사례 | 실제 API Trace의 핵심 순서 | Task 결과 |
| --- | --- | --- |
| 정상 | `backend/create_task` → `mcp/get_event_planning_context` (`planning_context`) → `supervisor/RUN_AGENT` (`routing`, `next_agent=event_planner_agent`) → `event_planner_agent/create_plan` → `supervisor/RUN_AGENT` (`routing`, `next_agent=balance_rule_agent`) → `mcp/get_game_rules`·`get_item_info` (`validation`) → `balance_rule_agent/validate_plan` → `supervisor/RUN_AGENT` (`routing`, `next_agent=critic_agent`) → `critic_agent/final_review` → `supervisor/WAIT_APPROVAL` (`routing`) | `WAITING_APPROVAL` |
| 기획 오류 주입 | `balance_rule_agent/validate_plan ERROR` → `supervisor/RUN_AGENT` (`recovery`, `next_agent=event_planner_agent`) → `mcp/get_event_planning_context` 재조회 → `event_planner_agent/regenerate_plan` → `supervisor/RUN_AGENT` (`routing`, `next_agent=balance_rule_agent`) → `mcp/get_game_rules`·`get_item_info` (`revalidation`) → `balance_rule_agent/validate_plan SUCCESS` | `WAITING_APPROVAL` |
| 첫 기획용 MCP 조회 지속 실패 | `mcp/get_event_planning_context ERROR` (`planning_context`) → `supervisor/RETRY` (`recovery`) 반복 → `supervisor/SAFE_STOP` (`recovery`) | `FAILED`, `current_result=null`; Planner·검증 Tool 미실행 |

정상 API 응답에서 첫 MCP 행은 `sequence=2`, `phase=planning_context`, `tool_name=get_event_planning_context`, `status=SUCCESS`, `data_source=primary`, `game_data_version=tinyheroes-1.0.0`으로 확인했다. 검증용 `get_game_rules` 행은 `phase=validation`이다. 실패한 첫 조회 행에는 확인되지 않은 `data_source`·`game_data_version`이 `null`로 나온다. `HANDOFF_ACCEPTED`, `WAITING_APPROVAL`, `candidate_saved` 같은 보조 행도 응답에 포함되므로, 프론트 지도는 필요한 actor/action만 선별해야 한다. `next_agent`는 Supervisor 행에만 있을 수 있으며 MCP 조회 행에는 넣지 않는다. Trace는 JSON payload로 저장돼 이 변경에 DB 테이블 변경은 없다.

아래 JSON은 **실제 API 응답에서 프론트 지도에 필요한 필드와 대표 행만 추출한 발췌본**이다. 원본 `traces`에는 위 표의 전체 행과 `time`, `reason`, `retry_count` 등 Schema의 나머지 필드가 포함된다. 따라서 아래 발췌본을 원본 응답 전체로 취급하지 않는다.

```json
{"task_id":"TASK-98AB9786C414","traces":[{"sequence":2,"actor":"mcp","action":"get_event_planning_context","status":"SUCCESS","phase":"planning_context","tool_name":"get_event_planning_context","data_source":"primary","game_data_version":"tinyheroes-1.0.0"},{"sequence":3,"actor":"supervisor","action":"RUN_AGENT","status":"SUCCESS","phase":"routing","next_agent":"event_planner_agent"},{"sequence":4,"actor":"event_planner_agent","action":"create_plan","status":"SUCCESS"},{"sequence":5,"actor":"supervisor","action":"RUN_AGENT","status":"SUCCESS","phase":"routing","next_agent":"balance_rule_agent"},{"sequence":7,"actor":"mcp","action":"get_game_rules","status":"SUCCESS","phase":"validation"},{"sequence":13,"actor":"balance_rule_agent","action":"validate_plan","status":"SUCCESS","phase":"validation"},{"sequence":14,"actor":"supervisor","action":"RUN_AGENT","status":"SUCCESS","phase":"routing","next_agent":"critic_agent"},{"sequence":16,"actor":"critic_agent","action":"final_review","status":"SUCCESS"},{"sequence":17,"actor":"supervisor","action":"WAIT_APPROVAL","status":"SUCCESS","phase":"routing"}]}
```

```json
{"task_id":"TASK-71527BFFDC47","traces":[{"sequence":13,"actor":"balance_rule_agent","action":"validate_plan","status":"ERROR","phase":"validation","error_type":"mission_unreachable"},{"sequence":14,"actor":"supervisor","action":"RUN_AGENT","status":"SUCCESS","phase":"recovery","next_agent":"event_planner_agent"},{"sequence":15,"actor":"mcp","action":"get_event_planning_context","status":"SUCCESS","phase":"planning_context"},{"sequence":16,"actor":"event_planner_agent","action":"regenerate_plan","status":"SUCCESS"},{"sequence":17,"actor":"supervisor","action":"RUN_AGENT","status":"SUCCESS","phase":"routing","next_agent":"balance_rule_agent"},{"sequence":19,"actor":"mcp","action":"get_game_rules","status":"SUCCESS","phase":"revalidation"},{"sequence":25,"actor":"balance_rule_agent","action":"validate_plan","status":"SUCCESS","phase":"revalidation"}]}
```

```json
{"task_id":"TASK-EB462F31CDB3","traces":[{"sequence":2,"actor":"mcp","action":"get_event_planning_context","status":"ERROR","phase":"planning_context","error_type":"mcp_timeout","data_source":null,"game_data_version":null},{"sequence":3,"actor":"supervisor","action":"RETRY","status":"SUCCESS","phase":"recovery"},{"sequence":9,"actor":"supervisor","action":"SAFE_STOP","status":"ERROR","phase":"recovery"},{"sequence":10,"actor":"backend","action":"safe_stop","status":"FAILED"}]}
```

세 시나리오는 `backend/tests/test_backend_a_planning_api.py`로 재현된다. 첫 두 발췌본은 `backend_b_mcp`와 실제 MCP HTTP 서버(로컬 JSON 데이터), Mock LLM을 거친 API 응답이다. 세 번째 안전 종료 발췌본은 A/B 통합 Mock 기반 API 응답이다. 어느 예시도 공용 PostgreSQL·실제 LLM을 모두 거친 응답은 아니다. 외부 연동 시험은 각 테스트 URL과 자격 증명을 갖춘 환경에서 별도로 해야 한다.
