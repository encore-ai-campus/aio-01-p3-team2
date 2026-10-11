# 오류 대응 정책과 Workflow 연결 재검사

검사일: 2026-10-11. 현재 `ikonly` 작업 트리를 기준으로 한다. 코드 변경 전 진단 기록이며 이 문서의 발견 사항을 수정 완료로 해석하지 않는다.

## 1. 판정

정책 pin·버전/hash, Supervisor 복구 결정, 상태/Trace 저장, 보안 hard block, 일부 Agent/MCP 재시도는 구현되어 있다. 하지만 **오류 대응 정책이 모든 실제 실행 경로를 일관되게 제어하는 구현은 아직 완성되지 않았다.** 이전 전체 테스트 통과는 이 누락 경로의 정책 적합성을 증명하지 않는다.

참조: [정책 설계](05_오류분류_복구정책_설계.md), [Workflow 설계](02_멀티에이전트_Workflow_설계.md), [이전 구현·검증 기록](14_Workflow_검사결과_설계보완_수정계획.md#7-2026-10-11-구현검증-기록).

## 2. 확인한 차이

| ID | 중요도 | 기대 계약 | 현재 동작·근거 | 필요한 보완 |
|---|---|---|---|---|
| POL-01 | 높음 | 수정 가능한 도메인 오류는 정책의 REVISE_PLAN 선택에 따라 수정·전체 재검증 | canonical_planning.py의 도메인 finding은 Plan 상태로 집계한다. 실제 정책 review_failure 호출은 MCP/Agent 기술 오류에 집중되어 있고 canonical 자동 기획 수정 루프는 없다. 기본 정책은 PLAN 오류에 REVISE_PLAN을 제공한다. 기존 Task 수정 루프와 새 canonical 경로의 지원이 다르다 | 고정 요구·미지원 범위·데이터 부족을 수정 대상으로 오인하지 않도록 분리한 뒤, 수정 가능한 finding만 정책에 연결. 미지원 action은 정책 UI/검증에서 적용 범위를 명시 |
| POL-02 | 높음 | LLM timeout/throttle/5xx와 인증·설정 영구 오류를 구분 | llm_service.py는 TimeoutError를 제외한 transport 예외를 LLMExecutionError로 합친다. canonical execute_agent는 이를 llm_execution_error / ERR-DEP-LLM-002(NEVER)로 분류한다. SDK가 최종 반환한 일시 오류도 Supervisor 정책 수준에서는 재시도 대상과 구분되지 않는다 | provider 예외를 일시·영구·보안·계약 오류로 정규화하고 동일 정책 결정 경로로 전달. SDK 내부 retry와 Supervisor retry의 중복 여부도 명시 |
| POL-03 | 높음 | fallback 호출도 해당 오류에 맞는 정책 결정·bounded retry를 거침 | canonical _run_mcp_read_async의 fallback 예외는 Supervisor review_failure를 다시 호출하지 않고 SAFE_STOP 기록 후 종료한다. fallback timeout의 routing 함수는 RETRY를 반환할 수 있지만 실제 fallback 실행부는 사용하지 않는다 | fallback 호출에도 정책·한도·권위 decision 저장을 적용. 동등성 미검증/invalid data는 계속 즉시 차단 |
| POL-04 | 중간 | 운영자가 허용한 재시도와 tool별 지원 범위가 일치 | 기본 MCP tool 오류 정책은 RETRY_OPERATION이다. 그러나 get_event_capabilities의 mcp_tool_error는 routing.py의 고정 tool 분기에서 SAFE_STOP으로 귀결한다 | 원인·tool별 retry/fallback 지원표와 정책 활성화 검증 연결. 재시도가 안전하지 않은 분기는 제한 이유를 UI와 Trace에 표시 |
| POL-05 | 중간 | 정책 문서의 matching·action·실행 한도가 실제 계약으로 구체화됨 | 현재 저장 가능한 route는 error code별 action/max_attempts뿐이다. phase/component match, fallback_target, REQUIRE_HUMAN, USE_FALLBACK, backoff 등의 설계 요소는 정책 편집 계약에 없거나 코드 상수/분기로 처리된다 | MVP에서 코드 불변조건으로 남길 것과 운영자 변경 필드를 명확히 구분하고 설계·UI·런타임을 동기화. 모든 필드를 즉시 설정화할 필요는 없음 |
| POL-06 | 중간 | 잔여 예외에도 실제 원인·위치에 맞는 오류 코드 적용 | workflow_execution._execute는 분류되지 않은 예외를 기본 llm_execution_error로, LookupError/ValueError를 input_validation_error로 처리한다. 모든 예외가 LLM/사용자 입력 원인인 것은 아니다 | storage/context/handoff/internal 오류를 정규화. 미분류 오류는 UNKNOWN으로 안전 종료하고 근거를 기록 |

미지원 게임 기능의 NEEDS_DEVELOPMENT, 데이터 부족, 고정 사용자 조건 위반은 무조건 자동 수정할 오류가 아니다. POL-01 보완 시 이 구분을 보존해야 한다. Deadline·예산·보안 hard block의 강제 종료도 운영자 정책으로 완화할 대상이 아니다.

## 3. 최소 재현

DB·외부 API 쓰기 없이 현재 Python 함수와 통제 coroutine만 실행했다. 새 테스트 파일은 추가하지 않았다.

1. `_default_policy()`의 `validate_policy()` 결과: 오류 없음.
2. 해당 정책을 실행 context에 고정해 `decide_recovery('mcp_tool_error', failed_tool='get_event_capabilities')` 호출: **SAFE_STOP**, retry_count=0. 기본 설정의 RETRY_OPERATION과 실제 tool 지원 범위의 차이를 확인했다.
3. 같은 정책에서 `decide_recovery('mcp_timeout', failed_tool='get_game_rules_fallback')`: **RETRY**, retry_count=1.
4. `_run_mcp_read_async`에서 primary가 MCPToolError, fallback이 MCPTimeoutError를 발생하도록 통제: primary **1회**, fallback **1회** 후 CanonicalMCPUnavailable. 정책의 timeout 총 시도 한도는 **4회**였으나 fallback의 정책 재시도 경로는 실행되지 않았다.
5. Planner 출력 계약 오류의 routing은 RETRY를 선택하는 것을 함께 확인했다. 모든 복구 경로가 미연결인 것은 아니다.

POL-01/02/05/06은 코드·설계 대조 결과이며 실제 외부 provider 장애 재현 결과로 표현하지 않는다. 사용자 DB·환경 파일·활성 정책은 변경하지 않았다.

## 4. 권장 수정 순서

1. 오류 정규화와 공통 `분류 → 정책 선택 → hard constraint 확인 → Supervisor 결정 저장 → 허용 command 실행` 계약을 먼저 고정한다.
2. LLM/MCP/fallback/저장/컨텍스트 예외가 이 계약을 거치는지 경로별로 검사한다.
3. 수정 가능한 도메인 finding의 수정 루프를 연결한다. 고정 요구와 미지원 기능은 결과 finding을 유지한다.
4. 정책 편집·활성화가 실제 실행 경로의 지원 action을 검사하게 한다.
5. 동일 오류에서 정책을 RETRY와 SAFE_STOP으로 바꿨을 때 **호출 횟수·다음 command·상태/Trace**가 달라지는 계약 테스트를 추가한다. 보안 오류의 완화 거부, fallback 재실패, 단계별 예산 소진도 포함한다.

상태 저장 구조를 다시 전면 교체할 문제는 아니다. 현재 Supervisor/공유 transaction/실행 예약을 유지하면서 누락된 정책 연결을 보완해야 한다.

## 5. 사용자 요청 화면 차단 확인

사용자가 모든 필드를 입력해도 `요청을 다시 확인하세요 / 입력값을 확인해주세요`가 표시된 사례를 로컬 사용자 서버에서 확인했다.

- `127.0.0.1:8004`의 기존 서버는 오래된 프로세스였다. 실제 OpenAPI는 action_id/reward_id를 필수로 요구하고 연결의 action_index/reward_index를 지원하지 않았다. 현재 UI는 ID 없는 요청·위치 기반 연결을 사용하므로 서버의 계약과 달랐다.
- 통제 요청은 HTTP 422와 `actions.0.action_id`, `rewards.0.reward_id`, `mission_reward_links.0.reward_id` 누락 및 위치 필드 extra 오류를 반환했다. UI는 응답의 필드별 detail을 표시하지 않고 일반 message만 표시한다.
- 기존 실행 명령과 동일한 포트·환경 파일을 사용해 백엔드만 최신 코드로 재시작했다. OpenAPI에서 ID가 선택 필드이고 action_index/reward_index를 지원하는 것을 확인했다. DB 초기화 및 활성 정책 수정은 하지 않았다.
- 재시작 후 같은 통제 요청은 ID 검증을 통과했으나 HTTP 503을 반환했다. 사용자 DB의 활성 정책 v21에는 `ERR-CONTRACT-CRITIC-001`, `ERR-CTRL-STATE-001`, `ERR-SEC-OUTPUT-001` 대응이 누락되어 pin 단계에서 거부됐다. Workflow 실행·외부 LLM 호출 전 차단이며 요청값 누락이 아니다.
- 응답 code는 RUNTIME_CATALOG_UNAVAILABLE였지만 실제 원인은 정책 계약 불일치다. POL-06의 원인 분류 보완에 포함한다. 활성 정책에 누락 경로를 보충한 초안을 검증·미리보기·활성화해야 실제 생성 검증을 이어갈 수 있다. 권장 경로는 Critic 계약 오류 RETRY_AGENT/max_attempts=4, 상태 충돌·응답 보안 오류 SAFE_STOP/max_attempts=1이며 보안 hard block은 유지한다.

현재 일반 오류 메시지만으로 입력 누락, 서버 계약 불일치, 정책 설정 오류를 구분할 수 없는 UX도 보완 대상이다.

## 6. 정책 연결 보완 구현

2026-10-11 후속 작업. §1~5는 수정 전 진단 기록이며 아래를 현재 구현 기준으로 한다.

| 항목 | 반영 내용 | 경계 |
|---|---|---|
| POL-01 | Critic의 PLANNER_EXPLANATION 판정이고 결정적 규칙 검증을 통과한 Plan만 정책 REVISE_PLAN으로 Planner → 규칙·경제·지원 검증 → Critic을 반복 | summary/action_rationales/reward_rationales만 수정. 사용자 행동·수치·보상·ID는 고정. 미지원 기능·데이터 부족·고정 조건 위반은 finding/사람 검토 |
| POL-02 | 연결 실패·408/409/429/5xx를 llm_transient_error → ERR-DEP-LLM-001로 분류하고 정책 재시도 | 인증·설정 등 영구 실패는 ERR-DEP-LLM-002로 종료. SDK max_retries=0, Supervisor만 재시도 결정 |
| POL-03 | canonical/기존 Task가 공통 MCP read 복구 루프를 사용. fallback 오류도 Supervisor 정책으로 다시 판단 | primary/fallback이 총 시도 예산을 공유하며 재귀 fallback 금지. 계약 오류는 fallback 금지 |
| POL-04 | get_event_capabilities/get_event_planning_context/get_game_rules_fallback tool 오류의 bounded retry 연결 | get_game_rules의 운영 오류에는 검증된 fallback만 허용. 응답·버전 불일치는 ERR-CONTRACT-MCP-001로 종료 |
| POL-05 | 현행 MVP 변경 필드와 코드 불변조건을 아래와 같이 명시. 정책 validate가 오류별 허용 action과 모든 코드의 대응 존재를 검사 | phase/component 매칭·운영자 지정 fallback·REQUIRE_HUMAN·backoff 편집은 현재 저장 계약의 기능이 아님 |
| POL-06 | storage/context/security/명시 오류 유형을 보존하고 미분류 예외는 UNKNOWN으로 종료 | worker catch-all은 실패 작업의 안전성을 증명할 수 없어 Workflow 전체 재실행을 하지 않음 |

### 적용 계약과 불변조건

- 운영자가 변경하는 route는 **오류 코드 → action/max_attempts**다. RETRY_OPERATION/RETRY_AGENT는 최초 실행을 포함한 총 시도 1~4회, REVISE_PLAN은 최초 기획을 포함한 1~3회다. SAFE_STOP/FAIL_FAST는 1회다.
- tool 허용 목록·fallback 동등성·고정 사용자 조건·보안/권한 guard·결과 불명 쓰기 금지·상태 CAS·deadline·LLM 호출/토큰/비용 한도는 코드 불변조건이다. 운영자 정책은 이를 완화하지 않는다.
- 재시도는 동일한 안전한 Agent/읽기/기존 저장 작업 경계에서만 실행한다. 임의 DB 장애의 catch-all에서 전체 Workflow를 재생하거나 결과 불명 쓰기를 반복하지 않는다. 저장 ACK 유실은 권위 결과 조회로 회수하고, 회수가 불가능하면 결과 불명 오류로 종료한다.
- 재시도 대기는 코드의 capped exponential jitter이며 운영자 편집 필드가 아니다. 정책 max_attempts는 이 상한과 전체 예산 안에서만 적용된다.
- 복구 결정은 Supervisor가 정책 버전·오류 코드·근거·카운터를 포함해 확정한다. canonical은 decision CAS 저장 뒤 command를 호출하며 기존 Task도 recovery event 저장 뒤 fallback을 실행한다.
- Critic의 repairability는 제안 값이다. Supervisor는 runtime_eligible과 수정 필드 제한을 확인한다. 기존 Critic 결과에서 해당 필드가 없으면 HUMAN_REVIEW로 처리한다.

### 코드와 검증 탐색

- 공통 MCP 실행: `backend/app/services/recovery_execution.py`; route: `backend/app/orchestration/routing.py`.
- canonical 수정 루프: `backend/app/services/canonical_planning.py`; 컨텍스트·카운터: `backend/app/agents/supervisor.py`.
- provider 분류: `backend/app/services/llm_service.py`; 잔여 예외: `backend/app/faults/error_catalog.py`, `workflow_execution.py`.
- 정책에 따라 호출 횟수/상태가 달라지는 테스트: `backend/tests/unit/test_policy_workflow_connections.py`. 기존 `test_workflow.py`, canonical/실행 예약/PostgreSQL 통합 회귀 검증도 함께 수행한다.
- 외부 provider 장애는 통제 transport로 검증했다. 실제 OpenAI 장애·rate limit을 발생시킨 검증으로 표현하지 않는다. 정책 UI와 복구 시연 화면의 별도 사용자 검토는 다음 단계다.

### 검증 결과

- `infra/test-local.ps1 -TestPath backend/tests`: **279 passed / 11 skipped**, 실제 PostgreSQL `eventquest_auto_test`, 100.70초. schema export 일치, Ruff check/format 모두 통과.
- 정책 차등 실행 전용 파일: 21개 검사. 정책 차단 API 검사 1개도 추가했다. 처음 전체 회귀 검사에서 기존 Task 재기획 대상 선택 회귀와 SDK fake 생성자 불일치가 발견돼 수정했고, 관련 34개 검사와 최종 전체 검사를 다시 통과했다.
- Edge 영향 E2E: `infra/e2e-local.ps1 -Browser edge -ApiPort 8006 -UiPort 5180 -TestName 'fault demo recovers|durable asynchronous workflow connects'` **2 passed**. 3개 복구 시나리오 각각 3회 반복과 비동기 생성→승인→게임 등록을 확인했다. 전체 UI 디자인·정책 편집 사용자 수용 검사를 완료했다는 뜻은 아니다. 임시 API/UI는 스크립트가 종료했다.
- 전체 검사 뒤 LLM 예산 예외의 worker 분류도 명시적으로 보충하고 정책 연결/실행 예약 영향 검사 **40 passed**, 변경 파일 Ruff check/format을 다시 통과했다.
- 외부 OpenAI/실제 MCP endpoint smoke 11개는 설정 부재로 제외했다. HTTP MCP 실패 통합은 테스트용 서버로 검증했으며 외부 서비스의 정상 연결 검증으로 표현하지 않는다.
- 사용자 DB/활성 정책, 환경 파일, `pre` 보존 문서는 변경하지 않았다. 구현 변경은 로컬 작업 트리에 있으며 별도 커밋하지 않았다.

### 사용자 환경 적용

사용자 DB의 활성 정책 v21은 유지한다. 기존 누락 3개 코드에 더해 새 `ERR-CONTRACT-MCP-001`(SAFE_STOP/1), `ERR-SEC-CONTEXT-001`(SAFE_STOP/1), `ERR-DOMAIN-PLANNER-002`(REVISE_PLAN/3)를 포함한 새 초안을 검증·미리보기·활성화해야 생성 실행이 가능하다. 보안 정책을 자동으로 덮어쓰지 않는다. 활성 정책 계약이 불완전할 때 POST event-briefs는 이제 **503 / RECOVERY_POLICY_INVALID**를 반환하며 입력 오류나 Runtime catalog 장애로 표시하지 않는다.

사용자 백엔드 8004를 동일 `backend/.env`와 native uvicorn 명령으로 재시작하고 `/health/ready`의 ready를 확인했다. 통제 요청은 실제 서버에서 503/RECOVERY_POLICY_INVALID로 구분되어 반환됐다. pin 단계에서 중단되어 Workflow 생성·외부 LLM 호출은 하지 않았다. 사용자 UI 5177과 DB는 그대로 유지했다.

## 7. 실제 Planner 실패와 provider 계약 동기화

2026-10-11 사용자 활성 정책 갱신 후 요청 `WF-B4CB6F3BCD6E`(18:12:03 KST)는 MCP 근거 조회를 완료하고 실제 OpenAI gpt-4.1-mini를 1회 호출했지만 ERR-CONTRACT-PLANNER-001로 종료했다. 정책 v166은 해당 오류에 SAFE_STOP/1을 설정했으므로 재시도 없이 종료한 것은 정책에 맞는 동작이었다.

원인은 provider JSON schema가 action_id/reward_id를 필수로 요구하는 반면 CanonicalPlannerOutput은 rationale만 허용하는 불일치였다. 저장된 응답을 검증해 action 3개/reward 3개의 ID 필드에서 extra_forbidden 6건을 확인했다. LLM이 임의로 잘못된 계약을 선택한 것이 아니라 provider 전달 계약의 동기화 누락이었다.

- `llm_service.py`의 canonical provider schema에서 action_id/reward_id 필드를 제거했다. Supervisor의 위치 기반 ID 결합과 권위 소유를 유지한다.
- `test_canonical_provider_schema.py`는 실제 provider schema의 필드·필수 목록·문자열 제약과 backend 모델을 대조한다. 이전 테스트가 provider 계약과 backend 모델을 별도로 검사하면서 이 차이를 놓친 점을 보완한다.
- UI는 planner.output_validated 성공 기록이 있을 때만 주 흐름의 Planner를 표시해 출력 검증 전 실패에서 Planner가 누락됐다. 실제 호출 직전 planner.started/STARTED를 저장하고 주 흐름에 표시하도록 보완했다. 이전 실패 기록은 실제 LLM 저장 참조가 있을 때만 호출 결과를 투영하고 handoff 요청만으로 실행 사실을 추정하지 않는다.
- 활성 정책은 자동 변경하지 않았다. 해당 오류의 재시도를 원하는 경우 운영자가 Agent 재시도 정책을 별도로 선택한다. 스키마 불일치 수정과 재시도 설정은 별개다.

영향 검사: provider/transport/정책 연결/실행 예약 44 passed, Edge 진행 UI 영향 2 passed. 이는 새 실제 OpenAI 기획안의 성공을 검증한 결과가 아니다. 사용자 재요청은 새로운 Workflow를 만들며 이전 안전 중단 기록을 덮어쓰지 않는다.

추가 PostgreSQL 영향 검사 3 passed: provider 계약 대조, Planner 실행 중 시작 기록 조회, 출력 계약 실패 후에도 시작 기록 보존. 50ms deadline 검사는 DB 지연에 따라 Planner 진입 전에 만료될 수 있으므로 시작 기록을 무조건 요구하지 않는다. schema export/Ruff check/format과 frontend production build도 통과했다. 사용자 API 8004는 수정 코드로 재시작했고 UI 5177은 유지했다.

## 8. 상태 저장 오류의 추가 누락 발견과 수정

2026-10-11 격리 시험실의 현재 활성 정책 시험에서 DB progress write의 정책 연결 누락을 추가 발견했다. 앞선 안전 중단 profile 검증은 현재 retry 정책을 입증하지 못했다. 실패한 저장만 재조정/소유권/CAS 확인 후 Supervisor 정책으로 재시도하며 일반 종료 저장 호출은 retry로 계산하지 않도록 보완했다. `ERR-DEP-MCP-003` 분리와 과거 정책 호환도 함께 반영했다. 최신 범위·시험 증거는 [시험실 16 §9](16_오류시험실_구현과_검증.md#9-2026-10-11-mcp-코드-분리와-상태-저장-복구-수정)를 따른다. 기존 검증 문장을 모든 활성 정책/모든 저장 경계의 증명으로 확대 해석하지 않는다.
