# Supervisor 상태 전이·복구 결정 통합 구현 계획

## 1. 목적과 결정

기획 Workflow의 권위 상태와 다음 실행·복구 결정을 단일 규칙 기반 Supervisor에 모은다. Controller와 각 API/service는 Agent·MCP·DB 작업을 실행하고 결과를 저장하지만 상태나 다음 행동을 독자적으로 결정하지 않는다. 사용자 입력 경로, 기존 API, canonical API는 하나의 Workflow 실행과 결정 계약을 사용한다. 과거 `WorkflowEngine` 경로는 별도 Backend나 영구 병렬 엔진으로 유지하지 않고, 공통 Workflow로 이행할 동안 필요한 입력·응답 호환 어댑터로만 취급한다.

정책은 Supervisor의 결정 입력이다. Workflow 시작 시 불변 정책 버전을 고정하고, 오류 코드·현재 단계·시도 횟수·deadline·검증 결과 및 근거 참조와 대조해 다음 상태·실행 명령을 결정한다. 보안·handoff 계약·쓰기 결과 불명·hard limit은 정책 편집으로 완화할 수 없는 서버 불변조건이다.

Supervisor는 단일 프로세스 전역 상태를 보유하지 않는다. 각 Workflow의 versioned snapshot을 입력받아 다음 snapshot을 반환하는 권위 있는 상태 결정자다. Controller는 상태 전이와 trace를 같은 DB transaction 안에서 낙관적 version 검사 및 idempotency와 함께 영속화한다. 이 구분은 여러 worker/process에서 상태를 공유하면서도 결정권을 단일화한다.

## 2. As-Is

- 본 절은 작업 전 기준이다. 작업 후 구현 현황은 9절의 진행표와 [현재 구현 상태](../현재_구현상태.md)를 따른다.
- 작업 전 `backend/app/agents/supervisor.py`의 `review_transition()`은 허용 action/Agent 이름만 검사하며 Workflow 상태를 갱신하지 않았다.
- 기존 Task API 실행기는 같은 FastAPI backend 안의 애플리케이션 경로다. 과거 문서의 “Backend B”는 팀 분업 당시 이름이며 별도 서비스·배포 단위가 아니다. 이번 변경 전에는 해당 경로가 자체 순서와 카운터를 관리하면서 복구 판단만 Supervisor를 호출했다.
- 작업 전 canonical 기획안은 `canonical_planning.py`에서 Workflow/Plan ID 발급, 순서 실행, 결과 상태 조립과 저장을 수행했다. 현재 Agent/MCP 복구 판단은 Supervisor를 거치며, canonical 상태 reducer 통합은 진행 중이다.
- 복구 정책은 실행 시작 시 pin하며 오류 코드별 허용 action과 시도 한도, hard cap을 검증한다.
- 작업 전 canonical Planner LLM의 action/reward ID echo는 제거됐지만 요청 schema와 프런트엔드가 ID를 공급했다. 현재 요청 schema는 위치 참조를 허용하고 Supervisor가 업무 ID를 발급한다.

## 9. 구현 진행 및 남은 범위

2026-10-11 재검사로 기존 통합 완료 판정을 재개방한다. 기존 테스트 117 passed와 격리 PostgreSQL 11 passed는 당시 실행 기록이며 아래 결함을 검증한 증거가 아니다. 최신 상세는 [Workflow 수정 계획 14](14_Workflow_검사결과_설계보완_수정계획.md)를 따른다.

| 단계 | 최신 판정 | 구현 사실과 남은 작업 |
|---|---|---|
| A. 계약 고정 | 보완 필요 | 공통 상태·decision 존재. 모든 종료·handoff·UI 이벤트 계약과 WAITING/AWAITING alias 정렬 필요 |
| B. 상태 결정자 | 부분 구현 | 규칙 reducer와 서버 업무 ID 발급 존재. 일부 deadline·budget 종료가 우회함 |
| C. 영속화 | 수정 필요 | CAS 존재하나 nested recovery 버전과 불일치 재현. 업무 변경과 상태·Trace 원자성, decision replay·실행 예약 보완 필요 |
| D. 실행 경로 통합 | 미완료 | 정상 흐름 공통 reducer 사용. 예외 종료·중단·활성 compatibility 경로 inventory와 같은 저장 계약 적용 필요 |
| E. 단일 backend | 구조 유지 | 하나의 FastAPI backend. 별도 Backend B는 추가하지 않고 공통 저장·실행 경계를 수렴 |
| F. UI·Trace | 보완 필요 | 완료 후 그래프·decision 상세 제공. 실행 중 진행 및 이벤트 명칭 일치 미완료 |
| G. 연결 검증 | 미완료 | 실제 engine+adapter+repository 복구, 원자성, 종료·중단·경합·접근·진행 화면 VG-01~09 추가 필요 |

외부 live smoke와 Edge 사용자 수용 외에도 구현 수정이 남아 있다. 재현 결함과 코드에서 확인한 저장·접근 누락을 수정한 뒤에만 완료 판정을 갱신한다.

## 3. 목표 계약

### 3.1 Supervisor 결정 입력

```yaml
workflow_id: string
state_version: integer
current_state: WorkflowState
event: WorkflowEvent
event_payload: object
error_code: string | null
evidence_refs: [string]
retry_count: integer
revision_count: integer
deadline_at: datetime
recovery_policy_ref: {policy_id: string, version: integer}
```

`event_payload`는 Schema로 검증된 안전한 요약만 담는다. 오류 원문·secret·전체 prompt를 상태 전이 근거에 포함하지 않는다.

### 3.2 Supervisor 결정 출력

```yaml
workflow_id: string
expected_state_version: integer
from_state: WorkflowState
to_state: WorkflowState
action: RUN_AGENT | RUN_TOOL | WAIT_APPROVAL | RETRY | TOOL_FALLBACK | SAFE_STOP | COMPLETE
next_agent: string | null
next_tool: string | null
retry_count: integer
revision_count: integer
policy_ref: {policy_id: string, version: integer}
matched_rule_id: string
reason_code: string
evidence_refs: [string]
decision_id: string
```

Supervisor가 정식 `action_id`, `reward_id`, `mission_id`를 발급하고 Brief 항목과 보상 연결을 보존한다. LLM과 브라우저는 내부 ID를 만들거나 권위 값으로 반환하지 않는다. Workflow/task/trace 요청 상관 ID는 요청 접수 경계에서 발급할 수 있지만 Supervisor snapshot에 등록한 뒤에만 해당 실행의 권위 ID로 사용한다.

### 3.3 상태와 이벤트

초기 canonical 상태는 `RECEIVED`, `VALIDATING_INPUT`, `COLLECTING_EVIDENCE`, `PLANNING`, `VALIDATING`, `CRITIC_REVIEW`, `AWAITING_APPROVAL`, `AWAITING_HUMAN_REVIEW`, `COMPLETED`, `REJECTED`, `CANCELLED`, `SAFE_STOP`을 포함한다. 기존 Backend B 값(`RUNNING`, `RETRYING`, `WAITING_APPROVAL`, `FAILED`)은 versioned compatibility mapping으로 canonical 상태에 투영한다. 승인 적용 단계는 `APPROVED`에서 시작해 Runtime 등록 결과 `APPLIED` 또는 복구 가능한 operation 상태를 별도 lifecycle event로 반영한다.

전이표는 허용된 `(current_state, event)`와 목적 상태·실행 action을 코드 계약으로 둔다. 복구 정책은 허용된 복구 action과 범위 안의 횟수만 결정한다. 상태 전이표 밖의 이벤트, 잘못된 정책 version, hard cap 초과, 보안/권한 위반은 `SAFE_STOP`한다.

## 4. 결정 근거·저장 계약

각 decision/transition은 `decision_id`, workflow/trace/task ID, from/to state와 version, event, error code, evidence refs, recovery policy version, matched rule, reason code, action, 카운터, 시각을 보존한다. 사용자 화면에는 상태·조치·짧은 설명을 보여주고 운영 상세에는 오류 분류와 정책 결정 근거를 펼쳐 보여준다.

Controller는 Supervisor decision의 `expected_state_version`과 현재 DB version을 비교한다. 일치할 때만 상태 snapshot과 transition trace를 같은 transaction으로 commit한다. 경쟁 업데이트는 재조회 후 새 Supervisor 판단을 요구한다. 동일 `decision_id`/operation idempotency key는 재실행 시 이미 기록된 결정 결과를 반환하며 Agent/Tool side effect를 중복 실행하지 않는다.

LLM 출력 schema 오류는 provider 연결/timeout과 별도 계약 오류 코드로 분류한다. 정책에서 bounded agent retry를 허용할 수 있지만 출력이 계속 유효하지 않으면 안전 종료한다. 외부 호출 전에 적용한 규칙 검증 결과와 snapshot을 근거로 남긴다. Critic의 의미 판단 자체가 구조화 근거를 제공하지 못하면 결정 권위를 부여하지 않고 `AWAITING_HUMAN_REVIEW`와 확인 가능한 finding 참조를 사용한다.

## 5. 구현 단계

| 단계 | 변경 | 주요 파일/산출물 | 완료 조건 |
|---|---|---|---|
| A. 계약 고정 | 상태·이벤트·decision Schema, 전이표, 오류 대응/근거 필드 및 호환 mapping 확정 | `backend/app/schemas/workflow_state.py`, 본 문서, Workflow/오류 설계 문서 | 모든 실행·terminal 이벤트가 전이표에서 유일하게 해석되고 미등록 이벤트는 거부 |
| B. Supervisor 상태 결정자 | pure transition reducer, 정책/하드 제한 검사, 이유·근거·정책 버전 포함 decision 생성, Supervisor ID 발급 | `backend/app/agents/supervisor.py`, routing 및 오류 registry, Brief 입력 Schema | 동일 입력·정책 version에 결정 재현, LLM/API/UI가 업무 ID를 발급하지 않음 |
| C. 영속화 Controller 경계 | 결정 실행기, version CAS, atomic transition+trace 저장, idempotency, old record reader | planning repository/service, lifecycle migration/schema | 동시 전이 충돌 및 ACK 유실 복구, 이중 side effect 없음 |
| D. Canonical 경로 통합 | API → Supervisor 전이 → controller command → Agent/MCP result → Supervisor 전이의 순환 연결; MCP 직접 routing 제거 | `canonical_planning.py`, `planning.py`, MCP helper | 모든 정상·오류·approval 입력이 Supervisor 결정 기록을 거침 |
| E. 단일 Workflow 수렴 | `WorkflowEngine`의 필요한 동작을 공통 Workflow로 이전하고 기존 API는 입출력 adapter로 전환 | `engine.py`, canonical planning/application service, repository adapter, 기존 Task 응답 mapping | 각 API 진입점이 같은 Supervisor reducer와 실행기를 사용하며 별도 상태/복구 분기가 남지 않음 |
| F. UI와 Trace | 사용자용 흐름과 펼쳐보기의 정책·근거 정보 연결 | `frontend-web/src/main.js`, workflow progress UI, API response schema | UI 상태와 DB snapshot/trace가 일치, policy version·reason/evidence 열람 가능 |
| G. 검증·문서 동기화 | unit/component/integration/E2E 단계별 전이·복구·경합·멱등성 시나리오와 설계/상태 문서 갱신 | `backend/tests/`, `frontend-web/e2e/`, `docs/ik/` | terminal 경로 전부 검증, 정책이 보안/계약 hard block을 완화하지 못함 |

의존 순서는 A → B → C → D/E → F → G다. D와 E는 서로 다른 API/DB adapter를 사용하므로 병렬 작업은 가능하지만 공통 상태/decision Schema(B)와 저장 계약(C)이 먼저 고정되어야 한다.

## 6. 회귀·이행

- 기존 DB workflow row와 Task 결과를 삭제·재작성하지 않는다. 새 `state_schema_version` 및 transition decision record를 additive migration으로 추가한다.
- 과거 레코드는 기존 status를 호환 mapping으로 읽고 새 상태 전이 이벤트를 소급 생성하지 않는다.
- 기존 승인·거절과 Runtime 적용의 exact plan version, DB 멱등성, Runtime side effect 경계를 보존한다.
- 복구 정책 활성 버전은 workflow 시작 때 pin하며 중간에 활성 버전이 바뀌어도 실행은 시작 때 정책으로 종료한다.
- `docs/ik/pre/`는 보존한다.

## 7. 수용 기준

1. Workflow 상태 변경 지점이 Supervisor decision을 생성하며 서비스/Controller/Agent가 상태를 직접 덮어쓰지 않는다.
2. Agent, MCP Tool, 사용자 승인/거절, 취소, timeout, 계약 오류, DB outcome unknown이 같은 event→decision→persist 흐름을 사용한다.
3. 복구 action은 고정 정책 version과 error code, 현재 phase, hard cap에 따라 결정되고 matched rule·reason·evidence·결과가 저장된다.
4. security, permission, invalid handoff, ambiguous write outcome, hard cap은 retry/fallback/revision 정책으로 완화되지 않는다.
5. concurrent transition은 state version CAS로 하나만 성공하며 loser는 새 상태를 다시 읽고 재판단한다.
6. idempotent replay는 같은 결정/결과를 반환하며 중복 Agent/Tool write를 실행하지 않는다.
7. 정상 승인·거절, Planner/Critic 실패, MCP timeout/fallback, retry cap, 사용자 취소 및 process restart 복구가 API/DB/Trace/UI 사이에서 일치한다.
8. 기본 진행 UI는 읽기 쉬운 상태만 보여주며 세부 패널에서 정책·오류 분류·근거 참조를 확인할 수 있다.

## 8. 문서 연결

- [멀티 에이전트·Workflow 설계](02_멀티에이전트_Workflow_설계.md)
- [오류 분류·복구 정책 설계](05_오류분류_복구정책_설계.md)
- [API·저장·Trace 설계](06_API_저장_Trace_설계.md)
- [보안·권한 설계](04_보안_권한_설계.md)
- [UI·UX 운영자 콘솔 설계](07_UI_UX_운영자콘솔_설계.md)
- [통합 검증 설계](09_통합검증_설계.md)
- 현재 구현 사실은 [현재 구현 상태](../현재_구현상태.md)를 따른다.


> 2026-10-11 수정 구현 및 실제 검증 범위: [수정 계획 14 §7](14_Workflow_검사결과_설계보완_수정계획.md#7-2026-10-11-구현검증-기록)을 최신 판정으로 사용한다. 이전 완료·재개방 표기는 당시 기록이다.
