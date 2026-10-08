# 게임 이벤트 AutoHealing 시스템 — 백엔드 B 개발 가이드

> 기준: 「개발 기획안」, 「게임 라이브 이벤트 기획·검수 AutoHealing 시스템」, 기존 「백엔드_B_개발_작성_가이드.txt」, 백엔드 A·B 합의 7개 사항, 「Backend_Contract_수정사항_팀공유용.md」. 이 문서는 MVP 구현 계약이다. 원본 TXT와 기존 MD는 보존한다. 프런트엔드의 실제 API·필드 사용 여부는 통합 전에 확인한다.

## 1. 역할과 경계

백엔드 B는 Event Planner, Balance & Rule, Critic, 코드 기반 Supervisor, Workflow Engine, Routing·Retry, Handoff, Python Rule Engine, MCP Client, Tool Guard, AutoHealing, Fault Injection, Workflow Trace 이벤트 생성을 담당한다. FastAPI Endpoint·PostgreSQL·ORM·SQL·승인 최종 확정은 A가 담당한다. B는 A의 Schema·Validation 및 Repository 인터페이스를 사용하고 HTTP 응답을 직접 만들지 않는다.

B 소유 영역은 `backend/app/agents/`, `orchestration/`, `rules/`, `services/llm_service.py`, `services/mcp_client.py`, `guards/tool_guard.py`, `fault_injection/`, `config/agents.yaml`, `config/security_policies.yaml`이다. A의 `schemas/`와 Repository 변경이 필요하면 필드·이유·예상 테스트를 먼저 전달한다. `security_policies.yaml`은 B가 작성하고 A가 Guard·Trace 관점에서 확인한다.

## 2. A·B 공통 계약 — 수정 시 양쪽 문서를 함께 갱신

### 2.1 공통 Enum과 오류

| 항목 | 값 |
| --- | --- |
| `TargetUser` | `new_user`, `returning_user`, `all_user` |
| `WorkflowStatus` | `RUNNING`, `RETRYING`, `WAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `CriticStatus` | `PASS`, `RETRY`, `FAIL` |
| `ApprovalStatus` | `PENDING`, `APPROVED`, `REVISION_REQUESTED`, `REJECTED` |
| `ApprovalAction` | `APPROVE`, `REQUEST_REVISION`, `REJECT` |
| `RoutingAction` | `RUN_AGENT`, `RETRY`, `TOOL_FALLBACK`, `WAIT_APPROVAL`, `SAFE_STOP`, `COMPLETE`, `CANCEL` |
| `FaultType` | `mission_unreachable_once`, `agent_failure_once`, `primary_tool_error_once`, `mcp_timeout_once`, `db_connection_error_once`, `validation_error_once`, `retry_limit_exceeded` |

Workflow·Trace 표준 `error_type`은 `mission_unreachable`, `item_not_found`, `event_brief_mismatch`, `validation_error`, `input_guard_error`, `tool_guard_error`, `response_guard_error`, `agent_failure`, `mcp_tool_error`, `mcp_timeout`, `db_connection_error`, `llm_timeout`이다. 내부의 `agent_execution_error`는 `agent_failure`로, MCP 연결·통신 Timeout은 `mcp_timeout`으로 매핑한다. 세부 원인은 `error_reason` 또는 Trace `reason_code`에 기록한다. Critic은 기술 오류를 판정하지 않는다.

### 2.2 요청·Agent 결과 Schema와 화면 표시 데이터

Event Brief는 `event_name`, `target_user`, `duration`, `objective` 네 필드다. `target_user`는 위 Enum, `duration`은 1~7이다. `actor_id`는 감사용, `idempotency_key`는 전송·처리용, `fault_type`은 Test 실행용이며 모두 Agent Context에서 제외한다.

```text
EventPlanResult
  target_user: TargetUser
  duration: int
  missions: list[Mission]
  rewards: list[Reward]
Mission = {day, content_id, mission_type, target_count}
Reward  = {day, item_id, quantity}
```

**내부 `EventPlanResult`에서는 `missions`와 `rewards`를 분리한다.** 기존 TXT의 `Mission` 안에 `item_id`, `quantity`를 넣는 형식은 내부 Schema로 사용하지 않는다. `event_name`과 `objective`는 원래 Event Brief에서 A가 유지하며 Agent Output 필수 필드로 새로 요구하지 않는다. B는 결과의 `target_user`·`duration`이 Brief와 일치하는지 검증할 수 있도록 반환한다. 모든 Mission·Reward의 `day`는 1~`duration` 범위여야 한다.

프런트엔드로 반환하는 `current_result`·`final_result`는 기존 Frontend API Contract를 유지한다. A가 B의 내부 Workflow 결과를 화면용 Event Result Schema로 변환하여 HTTP 응답을 구성한다. 기존 Contract가 `missions[]` 내부에 보상 표시 정보를 둔다면 A는 그 형태로 반환한다. 내부 Schema 분리 규칙을 화면 응답에 그대로 강제하지 않는다.

MVP의 Mission·Reward 연결 기준은 **같은 `day`를 우선 제안**한다. 다만 한 Day에 Mission 또는 Reward가 여러 개면 대응 관계가 모호해진다. 팀이 `day` 기준을 확정하기 전에는 Day별 일대일 대응을 Fixture·검증 규칙으로 제한하거나 별도 `mission_id` 연결자를 합의해야 한다. 임의로 다대다 결합하거나 보상을 복제하지 않는다.

기존 Frontend Contract의 화면 필드는 유지하며, 생성·확보 책임은 다음과 같다. A·B는 공통 Schema와 B Planner Output Schema에 필요한 필드를 함께 추가한다.

| 필드 | 책임 |
| --- | --- |
| `event_name`, `objective` | A가 원래 Event Brief 값을 유지 |
| `target_user`, `duration` | 원래 Brief와 B 결과를 검증 |
| `event_concept`, `operation_summary`, `day_title`, `planning_reason` | B Event Planner가 생성 |
| `content_id`, `mission_type`, `target_count` | B Planner 결과 |
| `mission_name` | B Planner가 화면 표시용 값 생성 |
| `content_name` | B가 MCP 검증 데이터에서 확보 |
| `item_id`, `quantity` | B Reward 결과 |
| `item_name` | B가 MCP 검증 데이터에서 확보 |
| 최종 `current_result`·`final_result` 및 HTTP Response | A가 기존 Frontend Contract에 맞게 구성 |

`day_title`과 `planning_reason`의 실제 배치 단위 및 선택/필수 여부는 Frontend Contract와 공통 Schema에서 확정한다. B는 표시 텍스트를 검증된 결과와 연결해 A에 전달하며, A가 표시 문구를 추측해 생성하지 않도록 한다.

`RuleValidationResult`는 `is_valid`, `error_type`, `reason`, `evidence`, `game_data_version`, `data_source`를 가진다. 여기에 프런트엔드 표시용 항목별 검증 결과 `checks[]`를 공통 계약에 반영한다. B가 규칙별 `type`, `status`, `message`를 생성하고 A가 Frontend Contract의 `rule_validation.checks[]`로 반환한다. `evidence`는 내부 검증 근거이며 `checks[]`의 대체물이 아니다. `is_valid=false`이면 오류 유형·설명·근거가 필수이고 `true`이면 `error_type=null`이다. `checks`가 없을 때는 `[]`를 사용한다. `CriticResult`는 `status`, `error_type`, `reason`을 가진다. PASS는 오류 유형이 null, RETRY는 오류 유형과 설명이 필수다. `RoutingResult`는 `next_agent`, `action`, `reason`, `retry_count`를 가진다. `WAIT_APPROVAL`·`SAFE_STOP`·`COMPLETE`·`CANCEL`이면 `next_agent=null`이다.

Handoff는 기존 기획안 형식인 `task_id`, `from_agent`, `to_agent`, `owner_agent`, `handoff_status`, `objective`, `inputs`, `constraints`, `expected_output`, `attempt`, `revision_count`, `retry_count`, `error_context`, `state_ref`를 사용한다. 상태는 `REQUESTED/ACCEPTED/REJECTED`다. 필수 입력과 Output Validation을 통과한 `ACCEPTED`에서만 `owner_agent`가 다음 Agent로 바뀐다. 기존 TXT의 `source_agent/target_agent/status/payload` 형식은 사용하지 않는다. RETRY Handoff에는 이전 오류·검증 결과·수정 요구사항을 담는다.

### 2.3 Engine·Repository 경계

```python
run(task_id, request, fault_type=None) -> WorkflowResult
revise(task_id, approval_comment) -> WorkflowResult
```

`run`의 `request`는 `actor_id`, `idempotency_key`, `fault_type`을 제외한 Event Brief다. A는 `revise` 전에 승인 상태·버전·승인자와 `approval_comment`를 검증하고 수정 요청 상태를 저장한다. B는 A의 Repository로 해당 `task_id`의 **최신 저장 요청·결과**를 조회한다. A가 요청·현재 결과의 사본을 `revise` 인자로 넘기지 않는다.

`WorkflowResult`는 `workflow_status`, `critic_status`, `approval_status`, `retry_count`, `revision_count`, `owner_agent`, `current_result`, `rule_validation`, `autohealing_events`, `error`를 담는다. B는 후보 결과와 상태 전이 요청을 반환한다. HTTP 응답·승인 최종 확정 및 실제 `result_version` 저장은 A가 담당한다. 첫 Critic PASS 후보 저장 때 버전은 0→1, Human Revision 후 새 PASS 후보 저장 때만 1 증가한다. B는 Trace 이벤트에 해당 저장 버전을 사용한다. 객체 없음은 `null`, 목록 없음은 `[]`로 표현한다.

B가 사용하는 Repository 계약은 `create_workflow`, `get_workflow`, `save_checkpoint`, `update_workflow_state`, `append_trace`, `save_idempotency_result` 중 Workflow에 필요한 함수다. 함수 목록은 유지한다. 개발 시작 전에 A·B가 각 함수의 Parameter Type·Return Type, sync/async 여부, Task 미존재 처리, DB Connection Error 공통 Exception, 동일 Write 재호출 시 중복 방지, DB Write 결과가 불확실할 때의 반영 확인 방식을 공통 인터페이스와 Fixture로 확정한다. 이 세부 계약이 정해지기 전에는 B가 반환 방식이나 예외명을 독자적으로 가정하지 않는다.

A의 Repository가 트랜잭션·고유 제약·중복 쓰기 방지를 책임진다. 일시적 `db_connection_error` 시 B Engine은 마지막 검증 완료 상태를 유지하며 **실패한 Repository 작업만 재호출**한다. 불명확한 쓰기 결과는 A Repository가 기존 반영 여부를 확인해 중복 없이 처리한다. B는 ORM Model·SQL·DB Connection을 직접 사용하지 않는다. 이벤트 생성은 Frontend `idempotency_key`, Approval은 `task_id + result_version + action` 기준의 기존 멱등성 정책을 유지한다.

## 3. Agent와 정상 Workflow

### Event Planner

입력은 Event Brief, 이전 오류·검증 근거, Human 수정 의견·현재 계획 중 정책상 허용된 값이다. `security_policies.yaml > context_access`에 허용된 필드만 전달한다. 대상·기간은 변경하지 않고 목적 방향을 유지한다. 초안·수정안은 Structured Output으로 받아 A의 Pydantic Output Validation을 통과한 뒤에만 다음 단계에 보낸다. Planner는 `event_concept`, `operation_summary`, `day_title`, `planning_reason`, `mission_name`을 화면 표시 데이터로 생성한다. 이 필드가 포함된 Output Schema를 A와 맞춘다.

### Balance & Rule 및 Python Rule Engine

Balance & Rule은 MCP 조회와 검증 자료 구성을 담당하고 **최종 결정적 판정은 Python Rule Engine**이 수행한다. 검증 범위는 콘텐츠 ID 존재, 필요 레벨과 `target_user`·해당 Day 예상 레벨 비교, Mission·Reward Day 범위, 아이템 ID 존재, 원래 Brief의 대상·기간 유지다. 항목별 검증 결과 `checks[]`와 내부 `evidence`를 함께 생성한다. `content_name`과 `item_name`은 검증된 MCP 데이터에서 확보한다. `target_count` 난이도, 보상 수량의 경제 밸런스, 실제 게임 로그·유저 자격, 이벤트 재화·상점 가격은 MVP 범위에서 제외한다. `game_data_version`과 `data_source`는 실제 조회한 데이터에서 기록하며 Agent가 추측하지 않는다.

### Critic

원래 Brief, 검증된 Event Plan, RuleValidationResult, 이전 RETRY 사유를 받아 대상·기간·목적·규칙 문제 해결 여부를 확인한다. 품질·기획 오류에 대해서만 `PASS/RETRY/FAIL`을 반환한다. 다음 Agent를 직접 선택하거나 계획을 수정하지 않는다.

### 정상 실행 순서

현재 구현·Mock 통합 시험의 순서는 `Planner → EventPlanResult Validation → Handoff ACCEPTED → Balance & Rule → Tool Guard → MCP → Tool Result Validation → Python Rule Engine → RuleValidationResult Validation → Handoff ACCEPTED → Critic → CriticResult Validation → WAITING_APPROVAL/PENDING`이다. 기획 전 조회를 추가할 목표 순서는 11절과 공통 `docs/architecture.md`를 따른다.

첫 승인 후보는 A가 `current_result`에 저장하며 `result_version=1`, `final_result=null`이다. `APPROVE/REJECT`는 A가 처리한다.

## 4. Supervisor·복구 규칙

Supervisor는 LLM Agent가 아닌 Python 규칙이다. 현재 State와 `error_type`을 보고 Routing·Retry·Fallback·Safe Stop을 결정한다. 미션을 직접 생성하거나 Critic을 대신해 품질을 판정하지 않는다. 기본 상한은 `max_revisions=2`, `max_retries=3`이다.

| 상황 | 처리 |
| --- | --- |
| `mission_unreachable`, `item_not_found`, `event_brief_mismatch` | 원래 Event Brief를 기준으로 Planner 재실행, `revision_count + 1`, EventPlanResult부터 Critic까지 전체 재검증 |
| `agent_failure`, 복구 가능한 Output/Handoff `validation_error`, `llm_timeout` | 실패한 Agent 또는 LLM 단계 재호출, `RETRYING`, `retry_count + 1` |
| `get_game_rules` 실행 오류·잘못된 결과 | `mcp_tool_error` 기록 후 Fallback Tool 사용. 기술 Retry가 아니므로 `retry_count` 증가 없음 |
| `get_item_info` 실행 오류·잘못된 결과 | `mcp_tool_error`, 동일 Tool 기술 Retry |
| `mcp_timeout`·서버 연결 실패 | 동일 Tool 기술 Retry. 같은 서버의 Fallback으로 우회하지 않음 |
| `db_connection_error` | 마지막 검증 완료 State를 보존하고 실패한 Repository 작업 재호출 |
| Tool Guard 정책 위반 | Tool 실행 차단, 동일 요청 자동 Retry 없음 |
| Critic FAIL, Fallback 실패, 재시도·수정 한도 초과 | Safe Stop → `FAILED`, 마지막 정상 상태·Trace 보존, 미검증 결과 폐기 |

`retry_count`는 현재 기술 장애의 **연속** 재시도 횟수이며 복구되면 0으로 초기화한다. `revision_count`는 AI가 기획 오류를 자동 수정한 횟수다. Human `REQUEST_REVISION`은 `revision_count`에 넣지 않고 `human_revision` Trace로 구분한다. Fallback도 Retry 횟수에 넣지 않는다. 검증되지 않은 부분 결과는 다음 시도에 재사용하지 않는다. MCP 서버 자체가 복구 한도 안에 돌아오지 않으면 기존 `task_id`는 `FAILED`로 끝내고, 서버 복구 후 새 요청·새 `task_id`로 시작한다.

## 5. MCP Client·Tool Guard·Context

현재 구현된 MCP Tool은 `get_game_rules`, `get_game_rules_fallback`, `get_item_info` 세 개다. Primary 게임 규칙 Tool의 실행 오류·Tool Result Validation 실패에는 Fallback을 호출한다. Fallback은 마지막 검증된 Snapshot이어야 하고 결과도 검증한다. `get_item_info`에는 MVP Fallback이 없다. MCP Timeout·연결 실패에는 같은 서버의 Fallback으로 우회하지 않는다. 기획 전 조회용 새 Tool은 11절의 추가 개발 계획이다.

Tool Result 필수 필드: 게임 규칙은 `content_id`, `content_name`, `required_level`, `game_data_version`; Fallback은 여기에 `source=fallback_snapshot`; 아이템은 `item_id`, `item_name`, `exists`다. Mock MCP의 `game_data_version`은 **`tinyheroes-1.0.0`**으로 통일한다. Mock Content ID는 `HUNT_001`, `DUNGEON_001`, `RAID_001`을 사용한다. `CONTENT_001`·`CONTENT_002`·`CONTENT_003`은 Mock Fixture와 Contract에서 교체한다. `data_source`는 실제 Primary 또는 Fallback 출처를 반영한다.

Tool Guard는 허용 Tool·인자·호출 조건을 검사한다. Agent에게 전체 Workflow State를 전달하지 않고 `security_policies.yaml`의 `context_access`에 있는 키만 Context로 구성한다. `actor_id`, `fault_type`, API Key, DB 정보, Secret, 전체 Prompt 원문은 Agent Context에 넣지 않는다. LLM Service는 Timeout, Structured Output·파싱, 실행 시간 측정, 내부 예외의 표준 오류 매핑을 맡는다. 실패 시 임의 계획을 만들어 성공 처리하지 않는다.

## 6. Human Revision·Trace·Fault Injection

A가 승인 조건·의견을 검증한 후 `revise(task_id, approval_comment)`를 호출한다. B는 Repository에서 원래 Event Brief·최신 저장 결과를 읽어 의견과 함께 Planner를 재실행하고, Output Validation → Balance & Rule → Critic을 모두 다시 수행한다. PASS 시 새 승인 후보를 A에게 반환한다. Human Revision은 `revision_count`를 늘리지 않는다. **재검증 실패 시 `FAILED`로 종료한다. 이전 승인 후보와 버전은 기록으로 보존하되 승인할 수 없으며, 실패한 수정안을 새 후보로 저장하지 않는다.**

B는 단계·상태 전이·오류·Retry·Fallback·Safe Stop에 대한 Trace 이벤트를 생성하고 A Repository에 저장 요청한다. 기본 정보는 `task_id`, 순서/시각, Agent·owner, action·status, 표준 `error_type`, `reason_code`, 정책 판단, `result_version`, 카운터, Tool/Fallback, `data_source`, `game_data_version`, 소요 시간이다. Agent/System 이벤트의 `actor_id`는 null이다. Human 수정 Trace의 감사용 `actor_id`는 A가 기록하거나 전달하되 LLM Prompt에는 넣지 않는다. API Key·비밀번호·전체 Prompt는 기록하지 않는다. MVP Tool 호출 정보는 Trace로 남긴다. 별도 Tool Log 테이블은 MVP에 필요하지 않으며, Frontend가 `/tool-logs`를 실제 호출한다면 A가 Tool 관련 Trace를 필터링해 반환하는 방식을 검토한다.

Fault Injection 허용 유형은 공통 `FaultType`과 같다. A가 `ENABLE_FAULT_INJECTION=true`를 확인한 Test API에서만 전달한다. `*_once` 장애는 최초 한 번만 주입하고 재실행에서는 정상 동작한다. `mission_unreachable_once`는 첫 Planner 결과에만, `primary_tool_error_once`는 Primary Tool 단계에만 적용한다. `retry_limit_exceeded`는 Safe Stop 확인용이다. Fault 설정은 Agent Context에 전달하지 않는다. 테스트 실행 결과는 A의 `test_runs` 저장 계약으로 기록한다.

## 7. 개발 순서와 검증

1. A와 Enum, 분리된 `missions/rewards`, Frontend 응답 변환, 화면 필드, `checks[]`, Handoff, `WorkflowResult`, `run/revise`, Repository 함수별 세부 계약을 동일 Fixture로 확정한다.
2. In-memory Repository, Mock LLM, Mock MCP를 만들고 `tinyheroes-1.0.0` 및 `HUNT_001`·`DUNGEON_001`·`RAID_001` Fixture를 사용한다.
3. Python Rule Engine, 세 Agent의 Output, Handoff, Routing·Supervisor를 정상 경로 순서로 구현한다.
4. 기획 오류 수정, 기술 Retry, Fallback, Safe Stop과 AutoHealing 이벤트·Trace를 추가한다.
5. Fault Injection을 한 유형씩 연결하고 Mock 단위 테스트를 통과시킨다.
6. 실제 MCP·LLM, A Repository·API 순으로 연결한 뒤 통합 시험한다.

필수 시험: 대상·기간 유지, Mission/Reward Day 범위 및 연결 기준, 화면 표시 필드 전달, `evidence`와 `checks[]` 동시 제공, 없는 콘텐츠·아이템, 레벨 미달, Critic 계약, Handoff REJECTED에서 소유권 미이전, 기획 오류 후 전체 재검증, Agent·Validation·MCP Timeout 1회 실패 후 복구, 복구 시 `retry_count=0`, Primary 오류 Fallback과 데이터 출처, Fallback 실패 Safe Stop, MCP 연결 실패 시 Fallback 미사용, Human Revision에서 `revision_count` 불변, Human Revision 실패 후 이전 후보 승인 불가, Fault 1회성·Test Mode 제한, Trace 민감정보 제거, A Repository 재호출의 중복 쓰기 방지.

## 8. 완료 기준과 Frontend 연동 확인

Mock Repository에서 정상 요청이 `WAITING_APPROVAL`까지 가고, 오류별 복구·Safe Stop이 계약대로 동작하며, A의 실제 API·DB와 연결해 정상·수정·장애 시나리오가 통과해야 한다. 최종 후보는 검증된 결과만 사용하고 `final_result` 확정은 A의 승인 처리에 맡긴다. PR 없이 협업하므로 B 소유 Engine·Agent·정책 파일을 A가 바꾸기 전에 변경을 공유하며, 공통 Schema·호출 규격 변경 시 양쪽 가이드·Fixture·테스트를 함께 갱신한다.

A의 우선 Core API: `POST /api/events/generate`, `POST /api/test/events/generate`, `GET /api/tasks/{task_id}`, `GET /api/tasks/{task_id}/trace`, `POST /api/tasks/{task_id}/approval`, `GET /health/live`, `GET /health/ready`. B는 이 API에 필요한 Engine·Trace·Fault Injection 계약을 제공한다.

아래 API는 Frontend의 실제 호출 여부를 확인한 뒤 MVP 범위를 정한다. B는 필요한 데이터·Trace 구조를 A와 조율한다.

| API | 범위 판단 |
| --- | --- |
| `GET /api/tasks?limit=20` | 최근 작업 목록 화면이 실제 사용하면 MVP 포함 |
| `GET /api/tasks/{task_id}/tool-logs` | 실제 사용하면 A가 Tool 관련 Trace로 응답하는 방식 검토; 별도 Tool Log 테이블은 필수 아님 |
| `GET /api/game-data` | Frontend 실제 사용 여부 확인 |
| `GET /api/system/health` | Frontend 실제 사용 여부 확인 |

## 9. 개발 전 공동 확인 항목

- [ ] Frontend가 `missions[]` 내부의 Reward 표시 정보를 사용하는지 확인
- [ ] Mission·Reward를 `day`로 연결할지, Day별 일대일 제한 또는 `mission_id`를 쓸지 확정
- [ ] 화면 필드의 Schema 위치·필수 여부와 B Planner 생성 방식을 확정
- [ ] `rule_validation.checks[]`의 타입·상태값·항목 목록을 Frontend Contract와 확정
- [ ] 최근 Task 목록, `/tool-logs`, `/api/game-data`, `/api/system/health`의 실제 Frontend 호출 여부 확인
- [ ] Mock `game_data_version=tinyheroes-1.0.0`과 Content ID 세 가지를 양쪽 Contract·Fixture에 반영
- [ ] Repository 함수별 Parameter/Return Type, sync/async, 미존재·연결 오류·중복 쓰기 처리 규격 확정

## 10. 2026-10-08 A/B 통합 결과와 B 후속 작업

이 절은 앞의 개발 계약 중 실제 `backend-test`에서 확인한 부분을 기록한다. 현재 통합 시험은 B Engine을 실행하지만 LLM·MCP 전송은 결정적 Mock이다. 실제 MCP 서버·OpenAI 연결 완료로 해석하지 않는다.

| 확인 항목 | 현재 연결 방식과 결과 | B가 변경할 때 확인할 것 |
| --- | --- | --- |
| Engine 호출 | A의 `BackendBWorkflowAdapter`가 동기 API에서 B의 async `run/revise`를 호출 | 입력 Brief와 `revision_loader`의 최신 결과 계약 유지 |
| 결과 Schema | Adapter가 B dataclass/dict를 A Pydantic 결과로 변환 | `missions/rewards`, `rule_validation`, 오류 필드 변경 시 A와 합의 |
| AutoHealing | B `action/error_type/agent/tool` 등을 A 응답 필드로 변환 | 새 액션은 A의 `recovery_action/target/revalidation_status` 매핑 테스트 추가 |
| Trace | B 이벤트를 A의 `trace_sink`가 Task sequence·저장 버전에 맞춰 기록 | Trace 필드·버전 의미 변경 전 A와 조율 |
| Swagger | 정상 생성·수정 요청·승인·거절 및 7개 Fault Injection 통과 | B 코드 수정 후 정상·오류 경로 모두 재시험 |

통합 중 AutoHealing 내부 이벤트를 A의 `AutoHealingEvent`로 바로 검증했을 때 HTTP 500이 발생했다. A Adapter에서 변환을 추가해 해결했다. B는 내부 이벤트 필드 변경 시 해당 변환을 깨뜨리지 않는지 A에게 알리고 통합 테스트를 갱신한다.

Fault별 관찰 결과: `mission_unreachable_once`는 자동 수정 1회, `agent_failure_once`·`mcp_timeout_once`·`validation_error_once`는 복구 후 승인 대기, `primary_tool_error_once`는 `fallback_snapshot`, `db_connection_error_once`는 Trace 저장 재시도 후 승인 대기, `retry_limit_exceeded`는 Retry 3회 후 `FAILED`였다. 자세한 API 응답은 `docs/team/backend-1/backend_ab_integration_test.md`를 참조한다.

최근 작업 목록의 동일 시각 정렬은 **A Repository 책임**으로 확정했다. A는 인메모리·PostgreSQL에 실제 생성 순번을 기록하고 `created_at` 동률일 때 그 순번으로 정렬한다. B는 임의 UUID 형식의 `task_id`를 생성 순서로 가정하지 않으며, `run/revise` 중 원래 `created_at`을 변경하지 않는다. 기본 백엔드 테스트는 `41 passed, 2 skipped`, 실제 PostgreSQL 선택 테스트는 `2 passed`였다. 기본 skip 2건은 테스트 DB URL 미설정 때문이다.

다음 B 연동 작업은 실제 MCP 서버의 세 Tool 응답·Timeout·Fallback과 출처를 확인하고, 이어 실제 LLM 전송을 별도로 검증하는 것이다. 이 단계가 끝나기 전까지 Mock 통과를 실제 외부 서비스 통과로 표시하지 않는다. B 소유 코드와 A 소유 Adapter·Repository 변경이 동시에 필요하면 두 담당자가 변경 필드와 테스트를 먼저 공유한다.

## 11. 기획 전 MCP 조회 추가 개발 계획 (2026-10-08)

초안 작성 전에 콘텐츠·아이템 ID가 정해지지 않는 문제를 해결하기 위해 MCP 팀에 `get_event_planning_context(target_user, duration)`을 요청한다. 이 Tool은 대상 유저의 일자별 예상 레벨, 사용 가능한 콘텐츠 ID와 입장 레벨, 보상 아이템 ID, `game_data_version`을 제공해야 한다. 정확한 응답 필드·타입·Fallback 방식은 MCP·Backend 팀이 계약으로 확정한다. 현재 MCP 서버에는 이 Tool이 없고, B Workflow에는 Mock으로 시험한 선택적 조회 경계만 있다.

B의 목표 실행 순서는 `이벤트 요청 → Backend의 기획용 MCP 조회 → Tool Result·버전 검증 → 제한된 Planner Context 전달 → Planner 초안 → 선택한 ID의 MCP 재조회·Python Rule Engine 검증 → Critic 검토 → 운영자 승인 대기`다. Event Planner의 Tool 직접 호출 금지 원칙과 외부 이벤트 생성 API의 네 입력 항목은 유지한다. Planner Context에는 검증된 기획 데이터만 전달하며 `actor_id`, `fault_type`, 비밀값은 전달하지 않는다. Human Revision과 자동 기획 수정에서도 새 초안을 만들기 전에 필요한 게임 데이터의 버전 일관성을 확인한다.

- 첫 조회의 통신 오류·Timeout은 Supervisor의 기존 기술 재시도·한도·Safe Stop 규칙으로 처리한다. 검증된 기획용 Fallback이 계약된 경우에만 이를 사용하며, 끝내 유효한 데이터를 얻지 못하면 추측으로 초안을 생성하지 않는다.
- 기획안 규칙 위반은 Planner 수정 후 미션·보상 전체를 다시 검증한다. 기존 `revision_count` 상한을 유지한다.
- 기획 전 조회 버전과 선택 ID의 검증 조회 버전이 다르면 결과를 섞어 PASS 처리하지 않는다. 같은 버전으로 재조회·재검증하거나 Safe Stop한다. 재조회 범위와 한도는 계약 확정 전 임의로 정하지 않는다.
- B는 기획 전 조회와 기획 후 검증 조회가 구분되는 Trace 이벤트를 생성한다. A의 저장·응답 계약에 새 필드나 action이 필요하면 먼저 합의한다. Frontend는 확정된 Trace 순서를 화면에 반영한다.

B 소유 구현·시험 순서: 새 Tool 응답 검증과 Planner Context 필터링 → 조회 실패·Fallback·버전 불일치 테스트 → Workflow 순서 변경 → 기존 Balance & Rule 재조회와 전체 재검증 확인 → A Adapter·Trace·API 통합 시험. MCP Tool과 A 계약이 준비되기 전에는 Mock 전송으로 B의 순서·복구 테스트를 진행할 수 있다. 실제 MCP·LLM과 API 전체 흐름은 별도로 검증한다.

### B 소유 구현 상태

`WorkflowEngine`은 선택적 `planning_context_loader(target_user, duration)`가 주입되면 Planner 전에 조회한다. B 내부에서 사용하는 정규화된 자료는 `daily_expected_levels`, `available_contents`의 `content_id`·`required_level`, `reward_item_ids`, `game_data_version`, `source`이며 전체 유저에게는 `common_access_max_required_level`을 전달한다. 이 필드는 MCP의 ID별 응답을 검증·변환한 Planner 전용 자료다. Loader가 없으면 기존 A Adapter의 Mock 실행 순서를 유지한다.

MCP 담당자가 확정한 Tool 응답은 `target_user`, 정수형 `duration`, ID별 객체인 `contents`·`items`, 요청 대상 기준을 담은 `user_segments`, `game_data_version`, `source`다. B는 요청 대상·기간과 버전·출처를 확인하고, `contents`와 `items`의 ID·표시 이름·레벨을 검사한다. 신규·복귀 유저는 기간 내 1·3·5·7일 체크포인트의 `expected_levels`를 사용하며, 나머지 날은 기존 Rule Engine처럼 가장 최근 기준값을 사용한다. `all_user`는 `content_access_only`의 공통 접근 레벨을 사용한다. Planner에는 접근 가능한 콘텐츠 ID·입장 레벨, 보상 ID, 대상에 필요한 레벨 기준과 버전·출처만 전달한다. 기획용 대체 Tool은 이번 범위에 없으므로 `source=fallback_snapshot`은 기획용 응답으로 수용하지 않는다.

기획용 조회는 기존 기술 재시도 상한을 사용하고, 유효한 결과가 없으면 후보 없이 Safe Stop한다. 기획 전후 조회 버전이 다르면 현재 B 구현은 재조회 대신 Safe Stop한다. 정상·기획 오류 수정·첫 조회 실패·버전 불일치의 B Mock 시험이 추가됐다. 기획용 Fallback은 MCP 팀의 검증된 Snapshot 계약이 없어 구현하지 않았다. A의 `backend_b_mcp` 모드로 기존 Tool의 실제 API 호출·Fallback은 확인했지만, 새 기획용 Tool과 Loader 주입이 없으므로 새 순서의 API 경로는 미완료다.

A 커밋 `166c20e`의 `PlanningContextLoader.load`를 B Engine에 주입한 Mock 통합 시험에서 정수형 `duration` 전달, 기획 전 조회 Trace, Planner Context 정보 제한을 확인했다. A가 확인하는 기본 응답 필드가 유효하더라도 상세 기획 자료가 빠지면 B가 재시도 후 후보 없이 종료한다. 보상 아이템 조회의 `game_data_version`은 필수로 검증한다. 조회 전에 Day 오류가 발생해 버전이 없을 때는 기술 장애로 오분류하지 않고 Planner 수정 경로를 유지한다. A Adapter의 실제 Loader 주입과 MCP 서버의 새 Tool은 아직 없으므로 API 경로는 미완료다.

B Trace 이벤트에는 기획 전 `actor=mcp, action=get_event_planning_context, phase=planning_context`, 기획 후 ID 조회 `actor=mcp, phase=validation|revalidation`, Planner 생성·재생성, 규칙 검증, Critic 검토, Supervisor 수정 결정·재시도 기록을 추가했다. 최신 A `TraceEntry`/Adapter는 `phase`와 `tool_name` 저장·응답을 지원한다. 새 순서의 실제 API 응답과 순번·문구는 기획용 MCP Tool과 Loader를 연결한 뒤 통합 시험해야 한다.

Planner 지시에는 기획용 Context가 전달되면 허용된 콘텐츠·보상 ID와 일자별 예상 레벨을 기준으로 선택하도록 명시했다. 검증용 MCP 조회가 실패하면 B Engine은 `mcp/ERROR`와 `supervisor/RETRY` 또는 `supervisor/TOOL_FALLBACK`을 분리해 기록한다. 실패한 조회에 확인되지 않은 게임 데이터 버전과 출처를 기록하지 않는다. Mock 회귀 시험에서 Timeout 재시도, 보상 Tool 재시도, Primary 실패 뒤 Fallback의 Trace 순서를 확인했다.

실제 LLM 전송은 `backend/tests/test_backend_b_llm_live.py`로 별도 검증한다. `LLM_TEST_MODEL`과 `OPENAI_API_KEY`가 모두 설정된 경우에만 실행하며 기본 백엔드 테스트에서는 건너뛴다. 2026-10-08 모델 `gpt-4.1-mini`에서 Planner의 목록 내 ID 선택과 실제 Planner·Critic 응답을 사용한 `WAITING_APPROVAL` 도달 2건이 통과했다. MCP 응답은 시험 파일의 고정 자료다. 실행 명령과 모델 이름 주의 사항은 같은 폴더의 `handoff.md`에 있다. 실제 LLM·실제 MCP 서버·A API를 한 번에 거치는 전체 흐름은 A의 Loader 주입 후 검증한다.
