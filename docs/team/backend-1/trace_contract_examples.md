# 기획 전 MCP 조회를 포함한 Trace 응답 계약안

이 문서는 프론트엔드가 요청한 실행 순서와 `GET /api/tasks/{task_id}/trace`의 **목표 JSON 예시**다. 2026-10-08 현재 A는 `phase`·`tool_name` 저장 및 응답을 지원하지만, B Engine의 기획 전 조회와 MCP의 `get_event_planning_context` Tool은 아직 구현되지 않았다. 아래 세 사례의 전체 순서는 통합 시험 전까지 완료로 표시하지 않는다.

## 공통 규칙

- API 응답은 `{ "task_id": "...", "traces": [...] }`이며 DB 내부 열 이름과 무관하게 `sequence`, `time`, `actor`, `action`, `status`, `reason`을 반환한다.
- `sequence`는 Task 안에서 1부터 증가하는 순서다. `time`에는 시간대가 있는 ISO 8601 값을 사용한다.
- 현재 A 구현은 Task의 기존 Trace 개수로 다음 번호를 정한다. 단일 Workflow의 순차 기록은 검증됐지만, 여러 프로세스가 같은 Task에 동시에 기록하는 운영 환경에서 중복 방지는 DB 고유 제약·원자적 번호 할당을 추가해야 한다.
- `phase`는 MCP 조회일 때 `planning_context`, `validation`, `revalidation` 중 하나다. 다른 행은 `null`이다.
- `tool_name`, `game_data_version`, `data_source`는 확인된 값이 있을 때만 채운다. 실패한 호출에서 데이터 버전·출처를 추측해 넣지 않는다.
- 아래 JSON은 읽기 쉽도록 `null`인 선택 필드와 기존 카운터·감사 필드를 생략한 축약 예시다. 실제 Pydantic API 응답에는 선택 필드가 `null`로 포함될 수 있다.
- `backend/create_task`와 `backend/candidate_saved`·`backend/safe_stop` 행이 추가될 수 있다. 지도에서 보여줄 다섯 단계의 상대 순서는 반드시 유지한다.

## 1. 정상 실행

```json
{
  "task_id": "TASK-NORMAL",
  "traces": [
    {"sequence": 1, "time": "2026-10-08T10:00:00+09:00", "actor": "backend", "action": "create_task", "status": "SUCCESS", "reason": "작업을 생성했습니다."},
    {"sequence": 2, "time": "2026-10-08T10:00:01+09:00", "actor": "mcp", "action": "get_event_planning_context", "status": "SUCCESS", "reason": "기획용 게임 데이터를 조회했습니다.", "phase": "planning_context", "tool_name": "get_event_planning_context", "game_data_version": "tinyheroes-1.0.0", "data_source": "primary"},
    {"sequence": 3, "time": "2026-10-08T10:00:02+09:00", "actor": "event_planner_agent", "action": "create_plan", "status": "SUCCESS", "reason": "게임 데이터에 맞춰 초안을 생성했습니다."},
    {"sequence": 4, "time": "2026-10-08T10:00:03+09:00", "actor": "mcp", "action": "get_game_rules", "status": "SUCCESS", "reason": "선택한 콘텐츠를 조회했습니다.", "phase": "validation", "tool_name": "get_game_rules", "game_data_version": "tinyheroes-1.0.0", "data_source": "primary"},
    {"sequence": 5, "time": "2026-10-08T10:00:04+09:00", "actor": "mcp", "action": "get_item_info", "status": "SUCCESS", "reason": "선택한 보상 아이템을 조회했습니다.", "phase": "validation", "tool_name": "get_item_info", "game_data_version": "tinyheroes-1.0.0", "data_source": "primary"},
    {"sequence": 6, "time": "2026-10-08T10:00:05+09:00", "actor": "balance_rule_agent", "action": "validate_plan", "status": "SUCCESS", "reason": "규칙 검증을 통과했습니다.", "phase": "validation"},
    {"sequence": 7, "time": "2026-10-08T10:00:06+09:00", "actor": "critic_agent", "action": "review_plan", "status": "SUCCESS", "reason": "기획안 품질 검토를 통과했습니다."}
  ]
}
```

이 Task의 `workflow_status`는 `WAITING_APPROVAL`이다. Trace에 별도 승인 대기 행을 요구하지 않는다.

## 2. 기획 오류 후 수정·재검증

```json
{
  "task_id": "TASK-REVISION",
  "traces": [
    {"sequence": 1, "time": "2026-10-08T11:00:00+09:00", "actor": "mcp", "action": "get_event_planning_context", "status": "SUCCESS", "reason": "기획용 게임 데이터를 조회했습니다.", "phase": "planning_context", "tool_name": "get_event_planning_context", "game_data_version": "tinyheroes-1.0.0", "data_source": "primary"},
    {"sequence": 2, "time": "2026-10-08T11:00:01+09:00", "actor": "event_planner_agent", "action": "create_plan", "status": "SUCCESS", "reason": "초안을 생성했습니다."},
    {"sequence": 3, "time": "2026-10-08T11:00:02+09:00", "actor": "mcp", "action": "get_game_rules", "status": "SUCCESS", "reason": "선택한 콘텐츠의 입장 레벨을 조회했습니다.", "phase": "validation", "tool_name": "get_game_rules", "game_data_version": "tinyheroes-1.0.0", "data_source": "primary"},
    {"sequence": 4, "time": "2026-10-08T11:00:03+09:00", "actor": "balance_rule_agent", "action": "validate_plan", "status": "ERROR", "reason": "해당 Day의 예상 레벨로 입장할 수 없습니다.", "error_type": "mission_unreachable", "phase": "validation"},
    {"sequence": 5, "time": "2026-10-08T11:00:04+09:00", "actor": "supervisor", "action": "RUN_AGENT", "status": "SUCCESS", "reason": "Planner에게 수정안을 요청했습니다."},
    {"sequence": 6, "time": "2026-10-08T11:00:05+09:00", "actor": "event_planner_agent", "action": "regenerate_plan", "status": "SUCCESS", "reason": "입장 가능한 콘텐츠로 수정했습니다."},
    {"sequence": 7, "time": "2026-10-08T11:00:06+09:00", "actor": "mcp", "action": "get_game_rules", "status": "SUCCESS", "reason": "수정안의 콘텐츠를 다시 조회했습니다.", "phase": "revalidation", "tool_name": "get_game_rules", "game_data_version": "tinyheroes-1.0.0", "data_source": "primary"},
    {"sequence": 8, "time": "2026-10-08T11:00:07+09:00", "actor": "balance_rule_agent", "action": "validate_plan", "status": "SUCCESS", "reason": "수정안을 재검증했습니다.", "phase": "revalidation"},
    {"sequence": 9, "time": "2026-10-08T11:00:08+09:00", "actor": "critic_agent", "action": "review_plan", "status": "SUCCESS", "reason": "수정안 품질 검토를 통과했습니다."}
  ]
}
```

전체 Mission·Reward를 다시 검사한다. 예시에는 핵심 조회 행만 표시했으며 실제 호출한 다른 Tool도 별도 행으로 남긴다.

## 3. 첫 기획용 MCP 조회 실패 후 안전 종료

```json
{
  "task_id": "TASK-CONTEXT-FAIL",
  "traces": [
    {"sequence": 1, "time": "2026-10-08T12:00:00+09:00", "actor": "mcp", "action": "get_event_planning_context", "status": "ERROR", "reason": "MCP 서버 응답 시간이 초과됐습니다.", "error_type": "mcp_timeout", "phase": "planning_context", "tool_name": "get_event_planning_context"},
    {"sequence": 2, "time": "2026-10-08T12:00:01+09:00", "actor": "supervisor", "action": "RETRY", "status": "SUCCESS", "reason": "기획용 조회를 재시도합니다."},
    {"sequence": 3, "time": "2026-10-08T12:00:02+09:00", "actor": "mcp", "action": "get_event_planning_context", "status": "ERROR", "reason": "재시도 후에도 데이터를 확보하지 못했습니다.", "error_type": "mcp_timeout", "phase": "planning_context", "tool_name": "get_event_planning_context"},
    {"sequence": 4, "time": "2026-10-08T12:00:03+09:00", "actor": "supervisor", "action": "SAFE_STOP", "status": "FAILED", "reason": "검증된 기획 데이터가 없어 종료했습니다.", "error_type": "mcp_timeout"}
  ]
}
```

이때 생성 API의 Task 결과는 `workflow_status=FAILED`, `current_result=null`, `final_result=null`, `result_version=0`이다. HTTP 상태는 현재 계약상 새 Task 생성 성공인 201이며 Workflow 실패 여부는 Task 본문에서 판단한다. Planner 실행·검증 성공 행은 남기지 않는다.

## 담당 범위와 완료 조건

- MCP 담당: 기획용 조회 Tool·응답 계약과 Fallback 자료.
- B 담당: 기획 전 조회 → Planner → 검증용 조회 → Rule → Critic의 실제 순서, 각 호출·판정의 Trace, 오류·수정·버전 재조회 정책.
- A 담당: 위 필드의 API Schema·저장·변환, Task별 순서, 실패 시 잘못된 성공 Trace 방지, 최종 JSON 회귀 테스트.

세 사례는 B와 MCP 구현이 연결된 뒤 실제 `GET /api/tasks/{task_id}/trace` 결과로 다시 검증하고, 예시와 다른 action 이름은 Frontend 담당자와 합의해 갱신한다.
