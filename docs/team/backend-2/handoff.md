# 백엔드 B 작업 Handoff

최종 갱신: 2026-10-07 (Asia/Seoul)

## 목적과 작업 방식

이 문서는 다른 데스크탑이나 새 대화에서 백엔드 B 작업을 이어가기 위한 기록이다. 사용자가 구현 코드를 직접 파일에 붙여 넣는다. 안내자는 매 단계에 **수정할 파일의 절대 경로와 위치, 파일 전체 코드, 한글 독스트링, 확인 명령**을 제공한다. 사용자가 완료 사실을 알려주기 전에는 다음 파일이 작성되었다고 가정하지 않는다. 코드 변경은 사용자가 직접 수행하고, 이 Handoff는 진행에 맞춰 갱신한다.

이 데스크탑의 저장소 루트는 `/Users/son/개발/multi-agent-autohealing`이다. 다른 데스크탑에서는 `pwd`로 새 절대 경로를 확인해 안내 경로를 바꾼다.

## 확인한 자료와 현재 상태

- 저장소 기준: `AGENTS.md`, `docs/last/`의 최신 기획안 2개, `docs/frontend_backend_api_contract.md`, `docs/team/backend-2/백엔드 B 개발 가이드_업데이트.md`, `docs/team/frontend/implementation.md`.
- 별도 전달받은 참고 자료: `frontend_backend_final_agreement.md`, `Frontend_Backend_API_Contract_수정_제안서.md`, `백엔드 A 개발 가이드.md`. 이 파일들은 이전 데스크탑의 Downloads 폴더에 있었으므로 다른 데스크탑에서 접근 가능하다고 가정하지 않는다. 아래에 구현에 필요한 결론을 기록한다. 제안서의 제안은 팀의 최종 승인으로 간주하지 않는다.
- 2026-10-07 확인 시 `backend/app/`의 Python 파일, `backend/tests/`의 Python 파일, `backend/app/config/`의 YAML 파일, `mcp_server/`의 Python 파일과 JSON 데이터 파일은 모두 빈 뼈대였다. 실제 작업 전 다시 확인한다.
- 확인 당시 브랜치는 `youngmin`이며 `.gitignore`에 기존 미커밋 변경이 있었다. 이 변경은 이 작업에서 만들지 않았고 건드리지 않는다. `백엔드 B 개발 가이드_업데이트.md`도 당시 Git 미추적 파일이므로 다른 데스크탑에 자동으로 전달된다고 가정하지 않는다. 핵심 계약은 이 Handoff에 따로 기록했으며, 원본 가이드가 필요하면 파일을 별도로 동기화해야 한다. 다른 데스크탑에서 브랜치와 작업 트리 상태를 다시 확인한다.
- 문서 중 `README.md`는 초기 뼈대 단계라고 설명하며, Frontend 구현 현황은 `docs/team/frontend/implementation.md`에 별도로 기록되어 있다. 상태 판단은 실제 파일과 최신 문서를 대조한다.

## 백엔드 B 책임과 경계

B는 `backend/app/agents/`, `orchestration/`, `rules/`, `services/llm_service.py`, `services/mcp_client.py`, `guards/tool_guard.py`, `fault_injection/`, `config/agents.yaml`, `config/security_policies.yaml`의 Agent, Supervisor, Workflow, MCP 연동, Tool Guard, 규칙 판정, 복구, 장애 주입, Trace 이벤트 생성을 맡는다. A는 FastAPI, 공통 Schema·Validation, Input/Response Guard, PostgreSQL Repository·트랜잭션, 승인·멱등성, Trace 저장·조회, Health와 Backend 배포 설정을 맡는다. B는 ORM·SQL·DB 연결 및 HTTP 응답을 직접 구현하지 않는다. A 소유 파일이나 공통 Schema의 변경이 필요하면 먼저 변경 이유와 Fixture·테스트 영향을 공유한다.

Engine 진입점은 `run(task_id, request, fault_type=None) -> WorkflowResult`, `revise(task_id, approval_comment) -> WorkflowResult`이다. `request`는 `event_name`, `target_user`, `duration`, `objective`만 포함한다. `actor_id`, `idempotency_key`, `fault_type`, Secret은 Agent Context에 넣지 않는다. `revise`는 A Repository에서 해당 작업의 최신 저장 요청과 결과를 읽는다.

## 현재 개발 기준

- `TargetUser`: `new_user`, `returning_user`, `all_user`; 기간은 1~7일.
- 내부 및 API의 Mission·Reward는 별도 `missions[]`, `rewards[]` 배열을 사용한다. Mission의 핵심 필드는 `day`, `content_id`, `mission_type`, `target_count`; Reward는 `day`, `item_id`, `quantity`다. 화면에는 `mission_name`, MCP에서 검증한 `content_name`·`item_name`을 제공한다.
- 프런트엔드의 `event_concept`, `operation_summary`, `missions[].day_title`, `missions[].planning_reason`은 Optional 표시 필드다. 누락이나 null만으로 Workflow를 실패시키지 않는다. `event_name`과 `objective` 원문은 A가 Event Brief에서 보존한다.
- MVP Rule Validation은 `is_valid`, `error_type`, `reason`, `evidence`, `game_data_version`, `data_source`를 사용한다. 현행 Frontend API 계약과 A 가이드는 `checks[]`를 요구하지 않지만 B 가이드는 요구한다. **`checks[]`는 합의 전까지 필수 Schema나 성공 조건으로 확정하지 않는다.**
- Frontend는 Mission·Reward를 같은 `day` 카드에 표시한다. 같은 Day에 여러 Mission·Reward가 있을 때 개별 항목 간 연결 의미는 미정이다. 초기 Fixture는 Day별 Mission·Reward를 하나씩 두어 모호성을 피한다. 다중 항목 연결이나 보상 복제를 임의로 구현하지 않는다.
- Mock 콘텐츠 ID는 `HUNT_001`, `DUNGEON_001`, `RAID_001`; 아이템 ID는 `ITEM_001`~`ITEM_003`이다. 현행 저장소 계약의 Mock 데이터 버전은 `tinyheroes-1.0.0`이다. 별도 최종 정리안은 `opongheroes-1.0.0`도 권장하므로 팀 확정 전에는 저장소 계약과 일치하는 `tinyheroes-1.0.0`을 임시 기준으로 사용한다.
- Supervisor는 Python 규칙이며 기본 상한은 `max_revisions=2`, `max_retries=3`이다. `revision_count`는 자동 기획 수정만 세고 Human 수정 요청은 세지 않는다. `retry_count`는 연속 기술 재시도 수이며 복구 시 0으로 초기화한다. Primary 게임 규칙 Tool의 실행·결과 오류는 Fallback으로, MCP 연결·Timeout은 동일 Tool 재시도로 처리한다. 검증되지 않은 결과는 승인 후보로 저장하지 않는다.
- 첫 Critic PASS 후보가 실제 저장될 때 `result_version`은 0에서 1이 된다. Human 수정 후 새 PASS 후보가 저장될 때만 증가한다. 승인·거절과 실제 버전 저장은 A 책임이다. Human 수정 재검증 실패 시 `FAILED`로 종료하고 이전 후보는 기록으로만 보존한다.
- MVP API는 생성 2개, Task 조회, Trace 조회, 승인, Live·Ready의 7개다. Task 목록, 별도 Tool Log, Game Data, 종합 System Health API는 후속이다. Tool 실행 기록은 Trace에 남긴다.

## 통합 전 확정할 계약

1. `rule_validation.checks[]`의 MVP 포함 여부: 현행 API/A 가이드는 후속, B 가이드는 MVP 요구. 현재 작업은 후속으로 두는 임시 기준을 따른다.
2. 한 Day에 여러 Mission·Reward가 있을 때 연결·검증 규칙.
3. `evidence`의 자료형과 여러 실패 근거의 표현. 문서 예시에 객체 `{}`와 목록 `[]`이 섞여 있다.
4. A Repository의 함수별 인자·반환 타입, sync/async, Task 미존재 처리, 공통 DB 예외, 같은 쓰기 재호출의 중복 방지, 쓰기 결과가 불명확할 때 반영 확인 방식. 함수 목록: `create_workflow`, `get_workflow`, `save_checkpoint`, `update_workflow_state`, `append_trace`, `save_idempotency_result`.
5. A·B 공통 Pydantic Schema, `WorkflowResult`, Fixture와 Trace의 저장 버전 규칙. 실제 A Repository·API 연결 전 같은 Fixture로 확인한다.
6. Mock 데이터 버전 문자열의 팀 최종 선택. 변경한다면 Frontend·Backend·MCP·Fixture를 함께 바꾼다.

## 권장 구현 순서와 진행 기록

1. 공통 상수·Fault Type 등 B 소유의 독립 파일부터 사용자가 직접 작성한다.
2. Mock 데이터/Mock MCP/Mock LLM과 In-memory Repository를 이용해 Python Rule Engine과 Agent를 작성한다. A 공통 Schema에 의존하는 부분은 A와 계약을 맞춘 뒤 연결한다.
3. Handoff, Routing·Retry, Supervisor, 정상 Workflow를 연결한다.
4. 기획 수정, 기술 Retry, Tool Fallback, Safe Stop, Trace·AutoHealing, Fault Injection을 추가한다.
5. A의 공통 Schema·Repository·API 및 실제 MCP·LLM과 통합하고 문서·Fixture·테스트를 함께 갱신한다.

진행 상태 (2026-10-07): **개발 안내 준비 완료, 사용자의 구현 코드 작성은 아직 확인되지 않음.** 다음 안내 후보는 `backend/app/fault_injection/fault_types.py`의 전체 코드다. 사용자가 붙여 넣고 확인 결과를 전하면 이 항목에 완료 파일·검사 결과·다음 작업을 기록한다.

## 다른 데스크탑에서 재개할 때

1. 저장소 루트에서 `pwd`, `git status --short --branch`, `git diff --stat`으로 위치·브랜치·기존 변경을 확인한다. `main`에 직접 작업하지 않는다.
2. 이 문서와 `docs/team/backend-2/백엔드 B 개발 가이드_업데이트.md`, `docs/frontend_backend_api_contract.md`, `docs/team/frontend/implementation.md`를 읽고 실제 파일의 구현 상태를 대조한다.
3. 사용자에게 다음 파일의 **절대 경로**, 파일 전체 코드, 코드의 역할, 확인 명령을 제공한다. Python 코드에는 기능과 입력·출력을 설명하는 한글 독스트링을 넣는다. 사용자가 직접 붙여 넣은 뒤 결과를 알려주면 다음 단계로 넘어간다.
4. 변경 후 `git status`, 의도한 파일의 diff, `ruff check .`, `ruff format --check .` 및 해당 기능에 필요한 테스트를 확인한다. 검사 도구가 설치되지 않았으면 `pip install -r requirements-dev.txt`가 기준이다. 문서·코드 상태를 일치시키고 이 Handoff를 갱신한다.
