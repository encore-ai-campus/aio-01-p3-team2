# 백엔드 B 작업 Handoff

최종 갱신: 2026-10-08 (Asia/Seoul). 2026-10-07 당시 상태는 아래 원문에 보존하고, 현재 추가 개발 계획은 마지막 절에 기록한다.

## 현재 브랜치와 소유 범위

- 작업 브랜치: `youngmin`. `main`은 변경하거나 push하지 않는다. `docs/team/backend-2/handoff.md`와 `백엔드 B 개발 가이드_업데이트.md`는 이 브랜치에서만 관리한다. 확인 당시 `main`에는 이 두 문서가 없고 `.gitkeep`만 있다.
- B 소유: `backend/app/agents/`, `orchestration/`, `rules/`, `services/llm_service.py`, `services/mcp_client.py`, `guards/tool_guard.py`, `fault_injection/`, `config/agents.yaml`, `config/security_policies.yaml` 및 B 기능 시험.
- A 소유: FastAPI, 공통 Schema·Validation, Repository·트랜잭션, 승인·멱등성, Trace 저장·조회, 배포 의존성. B는 ORM·SQL·HTTP 응답을 직접 구현하지 않는다.
- MCP 서버는 별도 MCP 팀의 `origin/port` 브랜치에서 구현 중이다. 이 컴퓨터의 `mcp_server/`에는 이전 단계에서 사용자가 직접 작성한 미커밋 Mock 파일이 있지만, 그 파일은 B 커밋에 포함하지 않는다. 이들 파일을 덮어쓰거나 MCP 팀 구현과 섞지 않는다.
- `.gitignore`의 기존 미커밋 변경도 B 커밋에 포함하지 않는다.

## 구현된 B 기능

- `agents/supervisor.py`, `orchestration/routing.py`, `retry.py`: Agent Context 허용 필드, 기획 수정 최대 2회, 기술 재시도 최대 3회, Tool Fallback·Safe Stop 경로.
- `agents/event_planner.py`, `critic.py`, `services/llm_service.py`: 필터링된 Context로 Structured Output을 받고 기본 응답 계약을 검사한다. 실제 OpenAI Responses 전송 함수 `create_openai_transport(model, agents_config)`를 제공한다. 모델 이름은 운영 설정에서 주입하며 API 키를 파일이나 Trace에 기록하지 않는다.
- `guards/tool_guard.py`, `services/mcp_client.py`: Agent별 Tool 허용 목록, 정확한 Tool 인자, Primary 실패 후 Fallback 허용, MCP 연결·시간 초과와 Tool 오류 분리. MCP Python SDK 1.x의 Streamable HTTP 전송 함수 `create_streamable_http_transport(url)`를 제공한다.
- `rules/game_rule_engine.py`, `agents/balance_rule.py`: Event Brief 대상·기간, Mission·Reward Day, 콘텐츠 접근 레벨, 아이템 존재 여부의 결정적 판정. MCP 팀의 실제 응답 구조인 `exists`, `user_segments`, `source`, `game_data_version`을 처리하고 없는 ID를 기획 오류로 구분한다. Primary 결과 오류 시 Fallback, 연결·Timeout 시 동일 Tool 재시도를 지원한다. 화면용 콘텐츠·아이템 이름은 검증된 MCP 응답에서만 확보한다.
- `orchestration/handoff.py`, `engine.py`: 검증 성공 후 Agent 소유권 이전, Planner → 규칙 검증 → Critic 실행, 기획 오류의 전체 재검증, Agent·Tool 재시도, Safe Stop, Human 수정 재검증, 최소 Trace·AutoHealing 이벤트, Fault Injection 연결. 실패한 수정안을 승인 후보로 반환하지 않는다.
- `fault_injection/fault_types.py`, `fault_manager.py`: `*_once`는 1회, `retry_limit_exceeded`는 반복 주입. A는 Test API에서만 `fault_type`을 전달해야 한다.

## 실행·어댑터 계약

- `WorkflowEngine.run(task_id, request, fault_type=None)`와 `revise(task_id, approval_comment)`는 **async**이며 B 내부 `WorkflowResult`를 반환한다. A는 실제 공통 Schema로 변환·검증하고 저장한다.
- `request`는 `event_name`, `target_user`, `duration`, `objective` 네 필드만 허용한다. `actor_id`, `idempotency_key`, `fault_type`, Secret은 Agent Context에 넣지 않는다.
- `revision_loader(task_id)`는 async이며 저장된 `user_request`, `current_result`, `revision_count`를 반환해야 한다. A가 승인 상태와 의견을 검증한 뒤 `revise`를 호출한다.
- `trace_sink(event)`는 async이다. 이벤트에는 `event_id`, `task_id`, 시각, actor, action, 상태, 표준 오류 유형, 재시도·수정 횟수를 포함한다. A가 sequence와 **실제 저장된** `result_version`을 부여하고 `event_id`로 중복 기록을 방지해야 한다. B가 넣는 `result_version=0`은 저장 전 임시값이다. 민감정보나 전체 Prompt는 전달하지 않는다.
- `plan_validator`, `rule_validator`, `critic_validator`는 A Output Validation을 붙이는 동기 콜백이다. 주입하지 않아도 B의 기본 구조 검사는 작동하지만, 실제 API 연결 시 A의 Pydantic 검증을 연결해야 한다.
- 현재 `backend/requirements.txt`는 비어 있다. 실제 전송 사용 시 A의 Backend 의존성 목록에 OpenAI Python SDK와 MCP Python SDK 1.x를 추가해야 한다. `origin/port`의 MCP 서버 요구사항은 `mcp>=1.27,<2`다. 실제 SDK·서비스 호출은 이 컴퓨터에서 의존성과 API 키·서버가 없어 실행하지 않았다.

## 검증 결과

- B 파일의 Ruff check 및 format check 통과.
- `python -m unittest discover -s backend/tests`로 B 관련 23개 Mock 시험 통과: 정상 승인 대기, 신규/복귀/전체 유저 레벨, 없는 콘텐츠·아이템, 기획 수정 한도, Agent·Validation·MCP Timeout 1회 복구, Primary Fallback, Fallback 실패 Safe Stop, Trace 일시 오류, Human 수정 성공·실패, Repository 조회 재시도, Handoff 거절 시 소유권 유지, SDK 응답 어댑터.
- `origin/port`에서 MCP 팀의 `_data.py`와 JSON을 임시 폴더로 추출해 B 규칙 판정에 연결했다. 정상 콘텐츠와 없는 콘텐츠의 판정, 버전 `tinyheroes-1.0.0`을 확인했다. 이 임시 추출 파일은 저장소 커밋에 포함하지 않는다.
- 저장소 전체 `ruff check .`는 B 외부의 `frontend/pages/2_event_result.py:135` 문법 오류 때문에 실패한다. 전체 `ruff format --check .`도 기존 Frontend 코드와 과거 Markdown 코드 블록의 포맷 차이 때문에 실패한다. B 브랜치에서 다른 팀 파일을 임의 수정하지 않는다.

## A·Frontend·MCP와 확정할 항목

1. `rule_validation.checks[]`: 현재 Frontend API 계약은 MVP 후속, B 가이드는 포함 요구. B는 항목별 `RuleCheck`를 내부에 보존하지만 공통 응답 필수 필드로 강제하지 않는다.
2. 한 Day에 여러 Mission·Reward가 있을 때의 연결·검증 의미. 현재 B는 각 배열 항목을 개별 검증하고 Day로 보상 복제나 다대다 연결을 만들지 않는다.
3. `evidence`는 B에서 첫 실패의 객체로 반환한다. 공통 문서의 객체/배열 혼용을 A·Frontend와 확정한다.
4. A Repository의 async 경계, 함수별 인자·반환, Task 미존재, 재호출 중복 방지, 실패 후 반영 여부 조회. 특히 `trace_sink`의 `event_id` 멱등 저장과 후보 저장 후 `result_version` 반영 방식이 필요하다.
5. A 공통 `WorkflowResult`, `EventPlanResult`, `RuleValidationResult`, `CriticResult`의 필드·상태값과 B 내부 dataclass 변환. 현재 A 소유 Schema·Repository 파일은 빈 뼈대라 실제 통합 시험은 아직 불가능하다.
6. MCP 팀의 `origin/port`가 통합될 때 B의 `create_streamable_http_transport`를 서버 URL(`/mcp`)에 연결하고 실제 SDK 호출·연결 장애를 시험한다. 사용자 로컬의 미커밋 `mcp_server/` 파일은 MCP 팀 버전과 JSON 키가 다르므로 그대로 병합하지 않는다.
7. 실제 OpenAI 모델·비용·API 키·Backend 의존성은 서비스 환경에서 확정한다. Mock 시험은 API 호출 요금이나 비밀값을 사용하지 않는다.

## 다른 데스크탑에서 재개하는 방법

1. `youngmin` 브랜치를 체크아웃하고 `git status --short --branch`로 의도하지 않은 변경을 확인한다. `main`에 직접 작업하지 않는다.
2. 저장소 루트에서 Python 3.11 가상환경을 각 운영체제에 새로 만든다. `.venv`는 Mac과 Windows 사이에 복사하지 않는다.
3. macOS: `.venv/bin/python -m pip install -r requirements-dev.txt`. Windows PowerShell: `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`.
4. B 시험: `.venv/bin/python -m unittest discover -s backend/tests -v` (Windows는 `python.exe` 경로로 변경). B Ruff: `.venv/bin/ruff check backend/app/agents backend/app/orchestration backend/app/rules backend/app/services/llm_service.py backend/app/services/mcp_client.py backend/app/guards/tool_guard.py backend/app/fault_injection backend/tests/test_backend_b_rules.py backend/tests/test_backend_b_transports.py backend/tests/test_workflow.py` 및 같은 경로의 `ruff format --check`.
5. A와 계약을 맞춘 뒤 어댑터를 연결하고 실제 MCP·LLM, 저장 버전·Trace, API·Frontend 시나리오를 통합 시험한다. 변경된 동작·설정은 이 문서와 공통 계약에 함께 반영한다.

## 2026-10-08 이후 추가 개발 계획

위의 브랜치·미병합 파일·비어 있는 의존성·과거 검사 실패 설명은 2026-10-07 당시의 기록이다. 현재 `backend-test`에는 MCP 팀 `port` 브랜치가 병합됐고, B `MCPClient`에서 실제 MCP 서버의 기존 세 Tool 직접 연결 시험이 통과했다. 이 직접 연결 결과가 API Workflow 전체의 실제 MCP 연결을 뜻하지는 않는다. 최신 흐름과 담당 경계는 공통 `docs/architecture.md` 및 이 폴더의 개발 가이드 11절을 따른다.

다음 B 작업은 기획 전 MCP 조회 결과를 검증하고 Planner에 허용된 데이터만 전달하는 경로를 Mock으로 먼저 시험하는 것이다. MCP 팀의 새 Tool 응답 계약이 정해지면 실제 조회로 교체하고, 기획 후 선택 ID의 재조회·버전 일치·복구·Trace를 시험한다. A 소유 Adapter·Schema·Repository·배포 의존성의 변경은 A와 계약을 맞춘 뒤 진행한다. 이 파일의 과거 23개 Mock 시험 및 당시 전체 Ruff 실패 기록은 현재 검사 결과로 사용하지 않는다.

B 소유 코드에는 위 조회·검증·Trace 경계가 추가됐다. 최신 A Adapter는 기존 MCP Tool의 실제 API 연결과 `phase`·`tool_name` Trace 저장을 지원한다. 새 MCP 기획용 Tool의 실제 HTTP 호출 결과는 A Loader와 B의 상세 검증을 통과했다. 다만 A Adapter가 Engine에 Loader를 전달하지 않아 새 순서의 API 통합 시험과 프론트엔드 요청 JSON은 아직 제공할 수 없다. MCP 팀은 기획용 대체 Tool을 이번 범위에서 제외했다.

프론트엔드 담당자에게 전달할 세 사례별 B Trace 확인 결과와 API 미완료 항목은 `docs/team/backend-2/frontend_trace_handoff.md`에 정리했다.

최신 A 커밋 `05ebeaf`와 MCP `port` 커밋 `3e0f661`을 로컬에 병합했다. 확정된 MCP 응답 필드에 맞춘 B의 기획 데이터 검증·Planner용 변환, 요청 대상·기간, 1·3·5·7일 기준 및 전체 유저 접근 기준, 상세 자료 오류, 보상 데이터 버전 누락과 Day 오류 수정 경로를 확인했다. 서버 URL을 지정하지 않은 `backend/tests`는 64 passed, 11 skipped이고, `mcp_server/tests`는 32 passed, 1 skipped(DB URL 미설정)였다. 별도 HTTP 서버에서 새 Tool→A Loader→B Engine의 정상 전체 실행과 Trace 순서를 확인했고, `backend/tests/test_backend_b_mcp_live.py`의 7건이 통과했다. 새 순서의 실제 API 응답은 A Loader 주입 후 검증해야 한다. 병합된 MCP 소유 파일의 Ruff 린트·포맷 검사 실패는 MCP 담당자에게 전달할 항목이다.

Windows PowerShell에서 실서버 B 시험은 MCP 서버를 실행한 뒤 `$env:MCP_TEST_URL="http://127.0.0.1:8010/mcp"`와 `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_backend_b_mcp_live.py -q`로 재현한다. 서버가 없으면 이 시험은 건너뛴다.

2026-10-08 오후 기준 실제 작업 브랜치는 `backend-test`다. 위 2026-10-07 원문의 `youngmin` 표기는 당시 기록이다. B의 기획 전 조회 구현·시험·문서는 `backend-test`에 커밋해 원격에 올렸고(`abe1c03`, `110507a`, `9d30c7e`), `origin/port`의 style 커밋도 병합했다. A는 `WorkflowEngine(..., planning_context_loader=...)`에 준비된 Loader를 주입할 수 있다. 병합 후 `ruff check .`는 통과했고, `ruff format --check .`는 MCP 소유 `mcp_server/tests/test_postgres_integration.py` 1건이 남아 MCP 담당자에게 전달할 항목이다.

실제 OpenAI 응답 시험은 `backend/tests/test_backend_b_llm_live.py`에 있다. `LLM_TEST_MODEL`과 `OPENAI_API_KEY`가 모두 있을 때만 실행하고, 없으면 2건을 건너뛴다. 호출마다 API 요금이 발생하므로 서버용 `OPENAI_MODEL`과 별도의 시험 전용 변수를 쓴다. MCP 응답은 시험 파일 안의 고정 자료이며 MCP 서버는 필요 없다. Windows PowerShell에서는 `OPENAI_API_KEY`를 환경변수로 설정한 뒤 `$env:LLM_TEST_MODEL="gpt-4.1-mini"`와 `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_backend_b_llm_live.py -q`로 재현한다. 키는 문서·코드·Trace에 기록하지 않는다.

2026-10-08 모델 `gpt-4.1-mini`로 2건이 통과했다. Planner는 전달한 `available_contents`·`reward_item_ids` 안의 ID만 선택했고, 실제 Planner·Critic 응답으로 기획 전 조회 → 초안 → 규칙 검증 → Critic PASS → `WAITING_APPROVAL`까지 진행했다. 모델 이름을 `openai/gpt-4.1-mini`로 지정하면 OpenAI가 404 `model_not_found`를 반환하고, B는 Planner 재시도 3회 뒤 `agent_failure`로 Safe Stop하며 승인 후보를 만들지 않았다. OpenAI SDK에는 접두사 없는 모델 이름을 사용한다. 이 결과는 1회 실행이며 응답 품질의 반복 측정이나 실제 MCP 서버·A API를 함께 거친 통합 검증은 아니다.

Planner가 고른 ID의 목록 포함 여부를 Engine에서 따로 사전 검증하지는 않는다. 목록 밖 ID는 기존 MCP 재조회와 Python Rule Engine 판정에서 기획 오류로 처리되고, 그 경로가 `evidence`와 검증용 조회 Trace를 남기기 때문이다.
