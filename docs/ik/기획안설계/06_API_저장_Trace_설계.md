# API·저장·Trace 설계

## 1. 목적과 범위

기획 요청부터 결과, 복구 정책 결정과 승인 후 Runtime 반영 요청까지의 API 경계와 데이터 소유권을 정한다. 구체 URL·SQL migration·JSON Schema 파일은 구현 계획 단계에서 이 논리 계약을 실제 저장소 구조에 맞춰 확정한다.

## 2. 현재 구현 기준

현재는 canonical 기획 API와 가상 게임 Runtime API가 별도 자원이지만 같은 PostgreSQL을 사용한다. Brief/Plan은 canonical workflow record로 저장하고, 결정은 immutable decision row로 저장한다. 승인·Runtime 등록은 별도 append-only lifecycle event로 같은 Workflow 조회에 합쳐 반환한다. 이 Trace는 UI 메모리에서 만들어내지 않는다. E2E 검증 및 아직 미완료인 전체 Trace 계약 범위는 [현재 구현 상태](../현재_구현상태.md), 적용 경계는 [게임 이벤트 적용 계약](../게임설계/05_이벤트적용_계약.md)를 기준으로 한다.

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
| `/recovery-policies` | GET 버전 조회, POST draft/validate/preview/activate/rollback | immutable version, schema/replay 결과, 영향 요약, actor·시각; 활성화/rollback은 `Idempotency-Key` 필요 |
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

## 7. Trace 계약

최소 이벤트 필드: `event_id`, `occurred_at`, `trace_id`, `workflow_id`, nullable `task_id`/`parent_task_id`, `event_type`, `phase`, `component`, `status`, `attempt`, `revision`, `plan_version`, `domain_schema_version`, `event_schema_version`, `game_data_snapshot_id`, `game_ruleset_version`, `capability_catalog_version`, `balance_policy_version`, `recovery_policy_version`, `error_code`, `reason_code`, `duration_ms`, `safe_attributes`. LLM invocation event에는 추가로 `provider`, `model_id`, `model_config_version`, `prompt_template_id/version`, `prompt_record_id`, `input_tokens`, `output_tokens`, `reported_cost`와 `pricing_version`을 기록한다. Provider가 token/cost 사용량을 반환하지 않는 경우 null과 사유를 기록하고 추정 사용량을 실제값으로 표시하지 않는다.

Trace는 요청 접수→task/handoff→검증/평가→복구 정책 결정→사람 결정→Plan→Runtime 등록 순서를 연결한다. 화면에 보여주는 단계 수는 사전 고정값이 아니라 실제 발생 이벤트와 현재 workflow 상태에서 계산한다. 기록 실패 시 필수 감사 이벤트는 업무 성공으로 표시하지 않는다.

현재 구현은 계획 생성 시의 immutable Trace와 승인·등록 이후의 `canonical_planning_events`를 Workflow 조회 시 시간순으로 합친다. 결정 이벤트 dedupe key는 workflow/plan version을, Runtime 등록 이벤트 dedupe key는 `runtime_event_id`를 기준으로 한다. Runtime 등록 후 API가 이벤트 Trace를 쓰지 못한 경우 요청은 실패로 반환되며, 같은 Runtime 등록을 다시 읽은 재요청에서 같은 event key로 기록을 보완한다. 전체 공통 Trace envelope의 모든 설계 필드와 불명확한 DB commit의 전 범위 outcome lookup은 아직 후속 수용 대상이다.

## 8. 보존·마스킹·환경 설정

실행에 사용한 렌더링 prompt와 LLM 원문 응답은 감사·재현을 위해 PostgreSQL의 제한된 저장 영역에 **30일간** 보존한 뒤 삭제한다. 일반 Trace에는 원문 대신 record reference, hash, 안전 요약과 prompt/model/version metadata를 둔다. API credential, transport header의 secret, 별도 비밀값은 prompt record에 포함하거나 저장하지 않는다. 사용자 Brief 원문은 prompt에 포함됐다면 해당 prompt record의 민감 데이터로 취급한다. 원문 조회는 초기 단일 운영자에게만 허용하고 조회 이력도 감사한다. 저장 영역과 backup은 운영 환경의 DB/호스트 at-rest 암호화를 통해 보호하며, 실제 적용 수단과 확인 증거는 구현 계획서에 기록한다. prompt 원문을 포함한 export/download는 기본 금지한다.

Redis는 첫 로컬 MVP의 필수 의존성으로 두지 않으며, 캐시가 필요하다는 측정 근거가 생기면 버전 지정 읽기 데이터만 대상으로 추가한다. DB partitioning과 정확한 API 경로도 배포 규모와 구현 구조를 확인한 뒤 구현 계획에서 결정한다.

## 9. 완료 기준 및 참조

- 각 쓰기 동작의 권위 저장소·transaction·멱등성 동작이 명시됨
- Plan의 버전/근거와 workflow·policy·게임 데이터 버전이 추적됨
- Runtime eligibility와 승인 버전이 서버 경계에서 재검증됨
- 캐시 장애·DB 쓰기 결과 불명·Trace 기록 실패의 동작이 정의됨
- [계약 설계](01_기획안_게임기능_계약_설계.md), [Workflow 설계](02_멀티에이전트_Workflow_설계.md), [오류 복구 설계](05_오류분류_복구정책_설계.md), [보안 설계](04_보안_권한_설계.md)를 따른다.
