# API·저장·Trace 설계

## 2026-10-11 정책 오류 응답 보완

POST `/api/v1/event-briefs`의 활성 정책 누락/계약 불일치는 Workflow 실행 전 **HTTP 503, detail.code=RECOVERY_POLICY_INVALID**로 응답한다. 입력 검증 오류 및 RUNTIME_CATALOG_UNAVAILABLE와 구분한다. 활성 정책은 자동 덮어쓰지 않고 운영자의 초안→검증→미리보기→활성화를 유지한다. MCP fallback과 설명 수정의 Supervisor 복구 결정도 다음 command 실행 전에 상태/Trace에 저장한다. [실제 적용 범위 §6](15_오류대응정책_Workflow_연결_재검사.md#6-정책-연결-보완-구현)을 따른다.

> 2026-10-11 검사 후 보완: 아래 내용은 설계 기준이며 구현 완료 선언이 아니다. 최신 보완 계약은 [Workflow 수정 계획 §3.2](14_Workflow_검사결과_설계보완_수정계획.md), 현재 사실은 [현재 구현 상태](../현재_구현상태.md)를 따른다.

## 검사 후 추가 적용 기준

승인 결정/Runtime 업무 변경과 Supervisor snapshot·decision·Trace·멱등 결과를 동일 PostgreSQL transaction으로 저장한다. 복구 결정도 최상위 권위 버전으로 저장한다. 생성 중 실행 예약·owner/lease/deadline과 UNKNOWN 재조정 절차를 추가한다. 현재 코드가 모두 충족한다는 뜻은 아니다.

구체 변경 위치·의존 순서·검증 증거는 [수정 작업 §4 및 검증 §5](14_Workflow_검사결과_설계보완_수정계획.md#4-구현-순서와-변경-범위)에 연결한다. 기존 계약과 중복되면 이번 보완의 더 엄격한 저장·접근·검증 조건을 적용한다.

## 1. 목적과 범위

기획 요청부터 결과, 복구 정책 결정과 승인 후 Runtime 반영 요청까지의 API 경계와 데이터 소유권을 정한다. 구체 URL·SQL migration·JSON Schema 파일은 구현 계획 단계에서 이 논리 계약을 실제 저장소 구조에 맞춰 확정한다.

## 2. 현재 구현 기준

현재는 canonical 기획 API와 가상 게임 Runtime API가 별도 자원이지만 같은 PostgreSQL을 사용한다. Brief/Plan은 canonical workflow record로 저장하고, 결정은 immutable decision row로 저장한다. `actor_id`는 서버의 단일 operator 설정에서 도출해 decision row와 lifecycle Trace에 기록하며 요청 본문 값은 인증 근거로 사용하지 않는다. 과거 actor 미저장 결정은 migration 010에서 `legacy-unknown`으로 표시한다. 승인·Runtime 등록은 별도 append-only lifecycle event로 같은 Workflow 조회에 합쳐 반환한다. 이 Trace는 UI 메모리에서 만들어내지 않는다. E2E 검증 및 아직 미완료인 전체 Trace 계약 범위는 [현재 구현 상태](../현재_구현상태.md), 적용 경계는 [게임 이벤트 적용 계약](../게임설계/05_이벤트적용_계약.md)를 기준으로 한다.

## 3. 원칙과 권위 데이터

| 데이터 | 권위 저장소 | 불변 조건 |
|---|---|---|
| Event Brief, Plan, Finding | PostgreSQL | 버전 증가, 이전 버전 보존, 최종 검증 참조 보존 |
| workflow/task/handoff 상태 | PostgreSQL | 전이 기록과 현재 상태가 함께 조회 가능 |
| 게임 카탈로그·규칙 버전 | 게임 도메인의 PostgreSQL 테이블/seed artifact | Plan은 사용한 버전 ID를 고정 참조 |
| 정책 정의·활성 버전 | PostgreSQL의 불변 policy version record | 활성화 시 버전 발급, 감사 이력 유지. YAML export는 감사/검토용 사본이며 별도 권위 원본이 아님 |
| idempotency key 및 결과 연결 | PostgreSQL | 리소스 생성/변경과 같은 트랜잭션 또는 outbox로 원자화 |
| 로그·Trace 이벤트 | PostgreSQL MVP; 규모에 따라 전용 관측 저장소 | 비밀/불필요 원문 제외, correlation ID 유지 |
| 캐시 | 선택적 Redis | 파생 데이터만 저장. cache miss/장애 시 권위 저장소로 안전하게 진행 |

정책 형식·action evaluator·hard cap은 소스 관리되는 코드와 schema로 검증한다. 활성화할 선언형 정책 본문은 PostgreSQL에 immutable version으로 저장하며, UI와 실행기는 이 registry만 읽는다. 파일로 export할 경우 읽기 전용 감사 사본으로 취급해 이중 원본을 만들지 않는다. Redis는 필수 구성 요소가 아니다. 도입하더라도 승인, 정책 활성화, Runtime 반영, 보상 지급의 권위나 멱등성 보장에 사용하지 않는다.

## 4. 논리 API 자원·요청 계약

| 자원 | 논리 동작 | 핵심 요청/응답 계약 |
|---|---|---|
| `/event-briefs` | POST 생성, GET 버전 조회 | 생성은 Brief 계약 필드와 `Idempotency-Key`; 응답 `brief_id`, `brief_version`, 정규화된 Brief |
| `/workflows` | POST 시작, GET 상태 조회, POST 취소/입력 제공 | 시작은 `brief_id/version`과 고정 context; 응답 `workflow_id`, `trace_id`, 실제 상태·현재 phase·발생한 progress event |
| `/plans` | GET 결과/버전 조회 | `plan_id`, `result_version`, runtime eligibility, findings, evidence 및 고정 context 버전 |
| `/decisions` | POST 승인/거절 | 승인 대상 `plan_id/result_version`, 결정, 선택적 코멘트, `Idempotency-Key`; 최신 결과·사용자 권한 재검사 |
| `/runtime-events` | POST 승인 결과 등록 요청, GET 등록 상태 | 승인된 Plan version을 `source_task_id + result_version`으로 해석해 게임 적용 계약에 전달; idempotency key와 adapter 결과 반환 |
| `/recovery-policies` | GET 버전 조회, POST draft/validate/preview/activate/rollback | GET은 immutable version·감사 기록·hard cap과 동일 오류 registry에서 만든 `error_catalog`(category, location, retry_class, 안전 메시지)을 반환한다. 변경은 schema/replay 결과, 영향 요약, actor·시각을 기록하며 활성화/rollback은 `Idempotency-Key` 필요. 활성화/rollback 결과는 동일 transaction의 operation 원장으로 회수한다 |
| `/traces` | GET Workflow/Task/Error 이벤트 조회 | correlation 필터, 안전한 요약; 제한 상세는 별도 권한 및 감사 기록 |

기본 API prefix는 `/api/v1`로 제안한다. 실제 framework route naming은 기존 앱과 충돌 여부를 확인해 구현 계획에서 매핑하지만, 동작 의미·필수 필드·응답 결과 의미를 바꾸지 않는다. Workflow 시작은 비동기 `202 Accepted`와 `workflow_id`를 반환하고, 동기 validation failure는 4xx, 처리 중 dependency failure는 해당 Workflow의 terminal state로 기록한다. 사용자 재개 입력은 기존 Workflow를 덮어쓰지 않고 명시적인 resume command/version으로 기록한다.

오류 응답은 다음 공통 envelope를 따른다. `field_errors`는 비필드 오류에서 빈 배열이며, retry 여부는 client의 재시도 지시가 아니라 서버 정책 결과다.

```json
{
  "error": {
    "error_code": "ERR-INPUT-API-001",
    "message": "요청을 확인해 주세요.",
    "field_errors": [{"path": "schedule.duration_days", "reason_code": "OUT_OF_RANGE"}],
    "trace_id": "opaque-id",
    "retryable": false
  }
}
```

내부 exception, prompt, secret, 전체 tool payload를 사용자 응답에 노출하지 않는다. 오류 코드 vocabulary는 [오류 분류·복구 정책](05_오류분류_복구정책_설계.md)을 따른다.

## 5. 저장 모델과 버전 관계

논리 엔터티는 `EventBrief`, `WorkflowRun`, `AgentTask`, `Handoff`, `EventPlan`, `ValidationFinding`, `HumanDecision`, `RecoveryPolicyVersion`, `ErrorOccurrence`, `TraceEvent`, `RuntimeRegistration`, `IdempotencyRecord`다. 실제 정규화/JSONB 배치는 query·migration 검토 후 결정한다.

모든 계획 결과는 `brief_id/version`, `workflow_id`, `domain_schema_version`, `event_schema_version`, `game_data_snapshot_id`, `game_ruleset_version`, `capability_catalog_version`, `balance_policy_version`, `recovery_policy_version`을 기록한다. Plan 재생성은 새 result version을 만들며 이전 승인이나 Runtime 등록을 새 버전에 자동 승계하지 않는다. 이벤트 적용 계약과의 세부 호환 규칙은 [게임 이벤트 적용 계약](../게임설계/05_이벤트적용_계약.md)을 따른다.

Runtime 등록은 `runtime_eligible=true`, 승인된 동일 `plan_id/result_version`, 연결된 `source_ref.task_id`, 지원 중인 게임 계약 버전을 모두 확인해야 한다. 기존 게임 적용 adapter의 권위 키는 `task_id + result_version`이므로 plan ID와 task ID의 매핑을 저장하고 승인 증거·digest가 일치하는지 서버에서 확인한다. 개발 필요 항목 또는 정책 차단 finding이 있으면 등록 endpoint도 거부한다.

## 6. 멱등성·원자성·캐시

- 외부에서 재시도될 수 있는 생성·결정·등록 요청은 `actor_id + operation_name + idempotency_key` 범위의 키와 canonical request hash를 받는다. operation은 `event.generate`, `task.decision`, `policy.activate`, `policy.rollback`, `runtime.apply`처럼 안정적인 식별자를 사용한다.
- 같은 scope/key와 같은 hash는 최초 결과를 반환한다. 같은 scope/key와 다른 hash는 conflict 처리한다. 별도 operation의 같은 raw key는 서로 충돌하지 않는다.
- DB commit 응답이 불명확하면 key로 결과를 조회한 뒤 결정하며 무조건 재실행하지 않는다.
- idempotency record, 업무 변경, 결과 참조는 가능한 한 같은 DB transaction에서 기록한다. 이벤트 등록/보상 기록은 `source_task_id + result_version` 등 unique business key와 DB transaction으로 중복 반영을 막는다. 비동기 외부 경계가 생기면 transactional outbox를 고려한다.
- 결과 record는 해당 업무 결과가 보존되는 동안 유지하고, 짧은 TTL 만료로 재실행을 허용하지 않는다. 업무 데이터 삭제가 허용되는 경우에도 키 재사용 방지 tombstone 기간을 별도 정책으로 둔다.
- Redis 캐시 허용 후보: 버전 지정 게임 카탈로그, 정적 규칙, 읽기 전용 capability lookup. key에 데이터 버전을 포함하고 TTL/명시적 무효화 방식을 둔다.
- 최신 정책, 승인 여부, 사용자 잔액, 미션 진행도, idempotency 결과는 캐시만으로 판정하지 않는다.

복구 정책 activate/rollback은 migration `011_policy_operation_idempotency.sql`의 `recovery_policy_operations`에 actor·operation·key·request hash·응답을 업무 변경 및 감사 row와 같은 PostgreSQL transaction으로 기록한다. 같은 키와 같은 대상은 저장 응답을 반환하고, 같은 키를 다른 버전에 재사용하면 conflict다. transaction advisory lock으로 동일 키의 동시 요청을 직렬화한다. commit 예외가 발생하면 새 연결에서 원장을 조회하고, 결과 확인도 불가능하면 `POLICY_OPERATION_RESULT_UNAVAILABLE`로 안전 응답한다. UI는 재시도 키와 요청 fingerprint를 sessionStorage에 유지해 페이지 새로고침 후에도 같은 요청을 재전송한다. PostgreSQL API 통합 검증은 commit 후 응답 유실, 동일 키 결과 회수, 변경·감사 단일 저장, 다른 버전 충돌을 확인한다.

## 7. Trace 계약

최소 이벤트 필드: `event_id`, `occurred_at`, `trace_id`, `workflow_id`, nullable `task_id`/`parent_task_id`, `event_type`, `phase`, `component`, `status`, `attempt`, `revision`, `plan_version`, `domain_schema_version`, `event_schema_version`, `game_data_snapshot_id`, `game_ruleset_version`, `capability_catalog_version`, `balance_policy_version`, `recovery_policy_version`, `error_code`, `reason_code`, `duration_ms`, `safe_attributes`. LLM invocation event에는 추가로 `provider`, `model_id`, `model_config_version`, `prompt_template_id/version`, `prompt_record_id`, `input_tokens`, `output_tokens`, `reported_cost`와 `pricing_version`을 기록한다. Provider가 token/cost 사용량을 반환하지 않는 경우 null과 사유를 기록하고 추정 사용량을 실제값으로 표시하지 않는다.

Trace는 요청 접수→task/handoff→검증/평가→복구 정책 결정→사람 결정→Plan→Runtime 등록 순서를 연결한다. 화면에 보여주는 단계 수는 사전 고정값이 아니라 실제 발생 이벤트와 현재 workflow 상태에서 계산한다. 기록 실패 시 필수 감사 이벤트는 업무 성공으로 표시하지 않는다.

현재 구현은 계획 생성 시의 immutable Trace와 승인·등록 이후의 `canonical_planning_events`를 Workflow 조회 시 시간순으로 합친다. 결정 이벤트 dedupe key는 workflow/plan version을, Runtime 등록 이벤트 dedupe key는 `runtime_event_id`를 기준으로 한다. Runtime 등록의 commit ACK 유실은 `request_id`로 저장 결과를 다시 조회해 회수하고, 후속 lifecycle Trace 쓰기가 실패하면 업무 상태 `APPLIED`와 `runtime_event_id`를 응답·Workflow 조회에 보존한다. 같은 등록 요청의 재시도는 Runtime 이벤트를 중복 생성하지 않고 누락된 Trace를 같은 dedupe key로 보완한다. PostgreSQL 통합 검사는 commit ACK 유실 후 replay와 두 번 연속 Trace 실패 후 세 번째 요청의 복구를 확인한다. Brief 생성은 idempotency key와 canonical body hash로 이미 저장된 workflow를 읽기 전용 조회해 commit ACK 유실 결과를 회수하며 Agent를 재실행하지 않는다. 승인/거절 결정과 정책 초안 저장은 같은 키로 결과 확인 재시도를 하고, 확인할 수 없으면 안전한 503을 반환한다. UI는 Brief/결정/Runtime Apply의 재시도 키를 sessionStorage에 유지하고 저장된 workflow/result 확인 후 제거한다. PostgreSQL 통합 검사는 Brief와 결정의 commit ACK 유실 및 단일 저장을, Edge E2E는 Brief/결정 재시도와 Apply 응답 유실 후 reload/replay를 확인한다. 이는 애플리케이션 예외로 commit ACK 유실을 재현한 검증이다. 강제 네트워크 단절, 장기/영구 Trace 장애의 결과 조회, 전체 공통 Trace envelope의 필드 추적성은 아직 수용되지 않았다.

2026-10-11 Supervisor 상태 통합: Task `traces`와 canonical lifecycle event는 `state_version`/`expected_state_version`을 검사하고 결정 snapshot과 Trace를 한 DB transaction에 추가한다. Task Trace migration `015_task_trace_state_version_cas.sql`은 기존 JSON payload의 버전을 열로 이관하고 `(task_id, state_version)` unique index를 추가한다. 오래된 worker의 버전은 `ERR-CTRL-STATE-001`로 거부하며 무조건 재시도하지 않는다. 격리 PostgreSQL에서 서로 다른 repository instance가 stale version을 제출하면 거부되고, 최신 snapshot을 읽은 다음 버전 결정은 저장되는 것을 확인했다. 실제 다중 process 동시 경쟁은 별도 운영 부하 검증이다.

## 8. 보존·마스킹·환경 설정

실행에 사용한 렌더링 prompt와 LLM 원문 응답은 감사·재현을 위해 PostgreSQL의 제한된 저장 영역에 **30일간** 보존한 뒤 삭제한다. 일반 Trace에는 원문 대신 record reference, hash, 안전 요약과 prompt/model/version metadata를 둔다. API credential, transport header의 secret, 별도 비밀값은 prompt record에 포함하거나 저장하지 않는다. 사용자 Brief 원문은 prompt에 포함됐다면 해당 prompt record의 민감 데이터로 취급한다. 원문 조회는 초기 단일 운영자에게만 허용하고 조회 이력도 감사한다. 저장 영역과 backup은 운영 환경의 DB/호스트 at-rest 암호화를 통해 보호하며, 실제 적용 수단과 확인 증거는 구현 계획서에 기록한다. prompt 원문을 포함한 export/download는 기본 금지한다.

Redis는 첫 로컬 MVP의 필수 의존성으로 두지 않으며, 캐시가 필요하다는 측정 근거가 생기면 버전 지정 읽기 데이터만 대상으로 추가한다. DB partitioning과 정확한 API 경로도 배포 규모와 구현 구조를 확인한 뒤 구현 계획에서 결정한다.

## 9. 완료 기준 및 참조

- 각 쓰기 동작의 권위 저장소·transaction·멱등성 동작이 명시됨
- Plan의 버전/근거와 workflow·policy·게임 데이터 버전이 추적됨
- Runtime eligibility와 승인 버전이 서버 경계에서 재검증됨
- 캐시 장애·DB 쓰기 결과 불명·Trace 기록 실패의 동작이 정의됨
- [계약 설계](01_기획안_게임기능_계약_설계.md), [Workflow 설계](02_멀티에이전트_Workflow_설계.md), [오류 복구 설계](05_오류분류_복구정책_설계.md), [보안 설계](04_보안_권한_설계.md)를 따른다.


## 10. Workflow 수정 후 실제 계약 (2026-10-11)

설계 보충과 실행 증거는 [수정 계획 14 §7](14_Workflow_검사결과_설계보완_수정계획.md#7-2026-10-11-구현검증-기록)을 따른다.

| 실제 endpoint/저장 | 계약 |
|---|---|
| `POST /api/v1/event-briefs` | `Idempotency-Key`; UI는 `Prefer: respond-async`로 202 및 Location/Workflow ID를 받는다. sync 호환은 같은 DB 예약 경로에서 완료 결과 201. 같은 key·다른 hash는 409 |
| `GET /api/v1/workflows/{id}` | 생성 중 마지막 확정 state/Trace, 종료 후 Plan/승인/Runtime 결과. `reconciliation_required`, `actual_effects`로 저장 상태와 실제 효과를 구분 |
| `POST /api/v1/workflows/{id}/cancel` | `expected_state_version` 정수. active 실행만 Supervisor CANCELLED로 변경; 이미 취소한 요청은 같은 결과 반환. 만료·완료 상태나 stale version은 conflict |
| `POST /api/v1/workflows/{id}/reconcile` | `expected_state_version`와 `Idempotency-Key`. 저장 승인과 실제 Runtime 등록 근거로 상태/Trace만 연결. 이벤트·보상 재실행 없음 |
| `workflow_operations` | migration `016`. key PK, Workflow unique, hash, owner, deadline, operation status, JSON snapshot·Trace. canonical 최종 결과와 FINISHED 전환을 같은 transaction에 저장 |
| 기존 Task Trace | nested recovery 목록을 권위 version 행으로 펼쳐 함께 commit. `new = expected + 1`, 같은 event/decision replay. `phase=handoff`, `recovery_policy_ref`는 추가된 필드 |
| DB 장애 응답 | mutation의 저장 결과를 확인할 수 없으면 503 + `operation_status=UNKNOWN`, 같은 key로 확인. read는 UNAVAILABLE. SQL/접속 정보는 응답하지 않음 |

승인과 Runtime 쓰기는 공유 `session_scope`로 업무 상태·전이·Trace·멱등 원장을 함께 기록한다. Agent/MCP 대기 중에는 transaction을 유지하지 않는다. active 정책은 실행 시작에 고정하며 policy/catalog/hard-limit hash를 참조한다. token/cost 한도와 공개 단가·pricing version도 hard-limit fingerprint에 포함한다.


기존 동기 Task/오류 시연 경로의 실행 owner·deadline은 Task JSON에 내부 필드로 저장하고 외부 응답에서는 제외한다. 생성/수정 시작 시 180초 deadline을 부여하며 결과 저장 직전 row lock 아래 owner·deadline을 검사한다. 만료 시 Supervisor SAFE_STOP, Task FAILED 투영, Trace와 owner 해제를 같은 transaction으로 기록한다. 과거 레코드는 updated_at 기준으로 만료를 판단한다. 공유 Task 저장과 Trace 저장 후 오류가 발생하면 양쪽 모두 rollback된다. 프로세스 중단·늦은 결과·반복 정리 검증은 수정 계획 14 §7을 따른다.
