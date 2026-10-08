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

## 2026-10-08 작업 종료 시점 인수인계

이 절은 2026-10-08 작업을 마친 시점의 상태다. 위 절들과 내용이 다르면 이 절을 기준으로 한다.

### 현재 상태

- 작업 브랜치는 `backend-test`이고, 이 절을 추가하기 직전 커밋은 `81fb0c8`이고 그때까지는 `origin/backend-test`에 올라가 있다. 이 절을 담은 문서 커밋 외에 미커밋 변경은 없다.
- A의 Loader 주입(`2b4e581`)을 병합했고, 실제 MCP 서버·실제 OpenAI를 붙인 API 통합 검증까지 마쳤다. 결과와 원본 Trace는 `frontend_trace_handoff.md`의 "2026-10-08 실제 API 통합 검증 결과"와 `trace_samples/`에 있다.

### 이날 `backend-test`에 올린 커밋

| 커밋 | 내용 |
| --- | --- |
| `abe1c03` | `WorkflowEngine`의 `planning_context_loader`, 기획 전 조회·검증, Tool Guard·MCP Client의 새 Tool 인자 |
| `110507a` | 기획 전 조회 Mock 시험과 실제 MCP 서버 Workflow 시험 |
| `9d30c7e` | `docs/architecture.md`의 기획 전 조회 흐름, B 가이드·handoff, `frontend_trace_handoff.md` |
| `9c684dd` | `origin/port`의 style 커밋 3개 병합 |
| `be71941` | 실제 OpenAI 시험 `test_backend_b_llm_live.py`와 실행 방법 문서 |
| `59167ee` | `docs/team/mcp/planning_context.md` (B가 정리한 계약 요약, MCP 담당자 확인 대상) |
| `81fb0c8` | `backend/.env.docker.example`의 기본 모델 이름, `mcp_server/.env.example`의 DB 주소 형식(비밀번호는 자리표시자) |

### 마지막으로 확인한 검사 결과

- `backend/tests`: 64 passed, 13 skipped. 건너뛴 항목은 `MCP_TEST_URL`, `LLM_TEST_MODEL`·`OPENAI_API_KEY`, 시험용 DB URL이 없을 때 실행하지 않는 시험이다.
- `mcp_server/tests`: 32 passed, 1 skipped(DB URL 미설정).
- `ruff check .`: 통과. `ruff format --check .`: MCP 소유 `mcp_server/tests/test_postgres_integration.py` 1건 실패. MCP 담당자에게 `ruff format` 적용을 요청해야 한다.
- 실제 OpenAI 시험: 모델 `gpt-4.1-mini`로 2 passed(1회 실행).
- 실제 MCP 서버 시험 `test_backend_b_mcp_live.py`는 이날 오후 7 passed였고, 이후 B 코드 변경은 없다.

### 환경 설정에서 주의할 점

- `backend/.env`와 `mcp_server/.env`는 `.gitignore` 대상이며 각 컴퓨터에서 직접 만든다. 이 컴퓨터의 `backend/.env`에는 `DATABASE_URL`, `MCP_DATABASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` 등이, `mcp_server/.env`에는 `MCP_DATABASE_URL`이 설정돼 있다. 값은 문서에 적지 않는다.
- `OPENAI_MODEL`은 `gpt-4.1-mini`처럼 접두사 없이 적는다. `openai/gpt-4.1-mini`는 404 `model_not_found`로 실패한다. 이 컴퓨터의 `backend/.env` 값이 접두사 없는 이름으로 고쳐졌는지 `live` 모드 실행 전에 확인한다.
- 이날 사용한 OpenAI API 키는 작업 도구의 대화 기록에 노출됐다. 아직 재발급하지 않았다면 폐기하고 새 키로 바꾼다.
- `backend/.env`에 `DATABASE_URL`이 있는 상태에서 `backend/tests`를 실행하면 `conftest.py`가 앱을 불러오며 PostgreSQL에 연결을 시도한다. DB가 꺼져 있으면 시험 수집 단계에서 연결 시간 초과로 멈춘다. 환경변수로 `.env`를 불러와 시험할 때는 필요한 변수만 설정한다.
- 이 컴퓨터에서는 pytest 기본 임시 폴더(`%LOCALAPPDATA%\Temp\pytest-of-<사용자>`) 접근이 거부돼 `tmp_path`를 쓰는 MCP 시험 4건이 오류가 난다. `--basetemp=<쓰기 가능한 폴더>`를 지정하면 통과한다. 코드 문제가 아니다.
- backend 가상환경에는 `pip install -r backend/requirements.txt`로 `openai` SDK를 설치해 두었다.

### 결정 사항

- Planner가 고른 ID의 목록 포함 여부를 Engine에서 사전 검증하지 않는다. 기존 MCP 재조회와 Python Rule Engine이 같은 오류를 잡고, 사전 검증을 넣으면 기획 오류의 `evidence`와 검증용 조회 Trace가 빈약해지며 새 Trace action에 대한 A 합의가 필요해지기 때문이다.
- 실제 LLM 시험은 서버용 `OPENAI_MODEL`이 아닌 시험 전용 `LLM_TEST_MODEL`로 켠다. 의도하지 않은 API 요금을 막기 위해서다.

### 다음 작업

1. 프론트엔드 담당자에게 `frontend_trace_handoff.md`의 통합 검증 결과와 `trace_samples/`의 실제 응답 JSON을 전달한다.
2. 공용 PostgreSQL에 게임 데이터가 적용되면 `MCP_DATABASE_URL`·`DATABASE_URL`을 설정한 상태로 같은 API 사례를 다시 실행한다. 이번 검증은 JSON 기본 데이터와 인메모리 저장소 기준이다.
3. MCP 담당자가 남은 포맷 1건을 고쳐 `port`에 올리면 병합하고 저장소 전체 Ruff를 다시 확인한다.
4. 실제 LLM의 기획 수정 사유 구체화는 지금은 하지 않는다. 기획 오류 주입 사례(`mission_unreachable_once`, 신규 유저 5일, `gpt-4.1-mini`)를 API로 6회 실행한 결과 5회는 수정 1회, 1회는 수정 2회로 모두 `WAITING_APPROVAL`에 도달했고 `FAILED`는 없었다. Planner에는 `error_type`과 일반 사유 문장만 전달되고 어느 Day·콘텐츠가 문제인지는 전달되지 않는다. 다른 대상·기간이나 모델에서 수정 한도 초과가 관찰되면 사유에 Day와 콘텐츠 ID를 담는 변경을 검토한다. 6회는 적은 표본이다.

### 진행 중: Supervisor 경로 결정 Trace

프론트엔드 요청으로 정상 경로의 Supervisor 결정(`phase=routing`, `next_agent`)과 복구 기록(`phase=recovery`)을 B Engine에 구현했다. 브랜치는 `backend-test`에서 만든 `feature/supervisor-routing-trace`다. A의 `TraceEntry`가 `phase`의 `routing`·`recovery` 값과 `next_agent` 필드를 받고 Adapter가 `next_agent`를 전달해야 하며, 그 전에는 `backend/tests/test_backend_a_planning_api.py` 3건이 Trace 저장 검증 오류로 실패한다. A 반영 후 이 브랜치를 `backend-test`에 병합하고 `trace_samples/`를 다시 수집한다.

### 다른 담당자에게 전달할 내용

- A: Loader 주입(`2b4e581`)을 병합해 실제 MCP·LLM으로 API 흐름을 확인했다. `backend/.env.docker.example`의 `OPENAI_MODEL` 기본값이 `gpt-4.1-mini`로 바뀌었다.
- MCP: `mcp_server/tests/test_postgres_integration.py`에 `ruff format`을 적용해 달라. `docs/team/mcp/planning_context.md`는 B가 정리한 문서이므로 실제 구현과 다르면 고쳐 달라. `mcp_server/.env.example`의 `MCP_DATABASE_URL`에 연결 형식 예시가 들어갔다.
- Frontend: 새 순서의 실제 API Trace JSON 7종이 `docs/team/backend-2/trace_samples/`에 있다. 사례별 설명은 `frontend_trace_handoff.md`의 마지막 절을 참고한다.

## 다른 데스크탑에서 이어서 진행하기 (2026-10-08 저녁 기준)

### 브랜치

| 브랜치 | 내용 | 상태 |
| --- | --- | --- |
| `backend-test` | A·B·MCP·Frontend 통합 브랜치. 기획 전 조회, API 통합 검증 결과, `trace_samples/` 포함 | 원격 `fa55a04`. `backend/tests` 67 passed, 13 skipped |
| `feature/supervisor-routing-trace` | `backend-test`에서 만든 B 작업 브랜치. Supervisor 경로 결정 Trace와 이 절을 포함 | 원격에 푸시함. `backend/tests` 69 passed, 13 skipped, 3 failed(A 변경 대기) |

`backend-test`는 2026-10-08 작업 중에 처음 만들어진 브랜치다. 이전 기록의 `youngmin` 브랜치 작업과 이날의 A·MCP·Frontend 병합, B의 기획 전 조회·실제 LLM 시험·API 통합 검증이 모두 여기에 있다. 이 문서의 2026-10-08 절들이 그날 작업의 맥락이다.

이어서 작업할 때는 `feature/supervisor-routing-trace`를 체크아웃한다. 이 브랜치는 `backend-test`의 내용을 모두 포함한다. `main`에는 직접 작업하지 않는다. 이 절은 두 브랜치의 `handoff.md`에 같은 내용으로 들어 있다.

```powershell
git fetch origin
git switch feature/supervisor-routing-trace
git status --short --branch
```

### 환경 준비

1. 가상환경은 컴퓨터마다 새로 만든다. 이 저장소는 `backend/.venv`, `mcp_server/.venv`, `frontend/.venv`를 폴더별로 쓴다.
   - `backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt -r requirements-dev.txt`
   - `mcp_server\.venv\Scripts\python.exe -m pip install -r mcp_server/requirements.txt pytest`
2. `backend/.env`와 `mcp_server/.env`는 Git에 없으므로 직접 만든다. 변수 이름은 `backend/.env.example`, `backend/.env.docker.example`, `mcp_server/.env.example`을 참고한다. `.env`는 자동으로 읽히지 않으므로 실행하는 셸에 환경변수로 설정한다.
3. `OPENAI_MODEL`은 `gpt-4.1-mini`처럼 접두사 없이 적는다. 2026-10-08에 사용한 OpenAI 키는 재발급 대상이다.

### 검사 명령

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
backend\.venv\Scripts\python.exe -m ruff check .
backend\.venv\Scripts\python.exe -m ruff format --check .
mcp_server\.venv\Scripts\python.exe -m pytest mcp_server/tests -q
```

- 셸에 `DATABASE_URL`이 설정된 상태에서 DB가 꺼져 있으면 `backend/tests`가 수집 단계에서 멈춘다. 시험할 때는 이 변수를 비운다.
- `feature/supervisor-routing-trace`에서는 `backend/tests/test_backend_a_planning_api.py` 3건이 실패하는 것이 현재 정상이다. 원인은 아래 "A에 요청한 변경"이다.
- `ruff format --check .`는 MCP 소유 `mcp_server/tests/test_postgres_integration.py` 1건이 실패한다.

### 실제 서버로 API를 확인하는 절차

2026-10-08의 API 통합 검증은 아래 절차를 자동화한 임시 스크립트로 실행했다. 스크립트는 저장소에 없으므로 같은 절차를 직접 실행한다. 이미 쓰는 포트와 겹치지 않게 임시 포트를 쓴다.

```powershell
# 1) MCP 서버 (JSON 기본 데이터, 별도 창)
$env:MCP_PORT="8011"
mcp_server\.venv\Scripts\python.exe -m mcp_server.server

# 2) Backend (인메모리 저장소, 별도 창). 실제 LLM은 backend_b_live와 OPENAI_API_KEY·OPENAI_MODEL
$env:WORKFLOW_ENGINE="backend_b_mcp"
$env:MCP_URL="http://127.0.0.1:8011/mcp"
$env:ENABLE_FAULT_INJECTION="true"
backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8001
```

3. `GET http://127.0.0.1:8001/health/ready`가 `{"status":"ready"}`인지 확인한다.
4. `POST /api/events/generate`에 `actor_id`(`liveops01`), `event_name`, `target_user`, `duration`, `objective`, `idempotency_key`(매번 새 UUID)를 보낸다. 장애 주입은 `POST /api/test/events/generate`에 `fault_type`을 더한다. 사용한 값은 `mission_unreachable_once`, `mcp_timeout_once`, `primary_tool_error_once`, `agent_failure_once`다.
5. 응답의 `task_id`로 `GET /api/tasks/{task_id}/trace`를 호출해 순서를 확인한다.
6. 첫 기획용 조회 지속 실패는 MCP 서버를 끈 상태에서 4번을 실행해 확인한다.
7. 확인이 끝나면 두 서버를 종료한다.

### A에 요청한 변경 (Supervisor 경로 결정 Trace)

B 구현은 커밋 `cb9f68a`에 있다. A가 아래 두 곳을 바꾸면 API 응답에 새 값이 나온다. 임시로 적용했을 때 `backend/tests` 전체가 72 passed, 13 skipped였고 실제 MCP 서버와 API로 5개 사례를 확인했다. A 소유 파일이므로 B가 커밋하지 않았다.

- `backend/app/schemas/workflow_state.py`의 `TraceEntry`: `phase` 허용 값에 `"routing"`, `"recovery"`를 추가하고 `next_agent: str | None = None` 필드를 추가한다.
- `backend/app/services/workflow_service.py`의 `_save_trace`: `TraceEntry(...)`에 `next_agent=self._optional_string(event.get("next_agent"))`를 전달한다.

Trace는 JSON payload로 저장되므로 DB 테이블 변경은 필요 없다.

### 이어서 할 일

1. A가 위 변경을 `backend-test`에 올렸는지 확인한다. 올라왔으면 `backend-test`를 받아 `feature/supervisor-routing-trace`를 병합하고 `backend/tests` 전체 통과를 확인한 뒤 `backend-test`에 올린다.
2. 위 절차로 정상·기획 오류·첫 조회 실패·검증 조회 재시도·Fallback·Agent 재실행 사례를 다시 실행하고, `docs/team/backend-2/trace_samples/`의 JSON을 `routing`·`recovery`·`next_agent`가 포함된 응답으로 교체한다. 지금 있는 7종은 이 변경 이전 응답이다.
3. `frontend_trace_handoff.md`를 실제 응답 기준으로 갱신하고 프론트엔드 담당자에게 전달한다.
4. 공용 PostgreSQL에 게임 데이터가 적용되면 `MCP_DATABASE_URL`·`DATABASE_URL`을 설정해 같은 사례를 다시 실행한다.
5. MCP 담당자가 포맷 1건을 고쳐 `port`에 올리면 병합하고 전체 Ruff를 다시 확인한다.

A 변경이 오래 걸리면 프론트엔드 담당자에게는 `frontend_trace_handoff.md` 마지막 절의 형식 표를 먼저 전달한다. A 반영 전의 미리보기 응답은 저장소에 보관하지 않았다.

## `feature/demo-game` 병합 후 확인 상태 (2026-10-08)

이 절은 위의 과거 브랜치·A 반영 대기·MCP 포맷 오류 기록보다 최신이다. 현재 작업 브랜치는 `feature/demo-game`이다. A `TraceEntry`에 `routing`·`recovery`와 `next_agent`가 반영됐고 Adapter도 B Trace의 `next_agent`를 저장한다. MCP 담당자의 PostgreSQL 테스트 포맷 수정 커밋 `d913b7a`도 병합되어 있다.

### 완료한 확인과 산출물

- Python 3.11 가상환경을 `backend/.venv`, `mcp_server/.venv`, `frontend/.venv`에 각각 만들고 해당 서비스 의존성과 Ruff를 설치했다. MCP 환경에는 문서의 시험 도구인 pytest도 설치했다. `.venv`는 Git에서 제외한다.
- 기본 실행에서 `backend/tests` 74 passed, 13 skipped, `mcp_server/tests` 32 passed, 1 skipped. Skip은 실제 LLM 키·MCP 시험 URL·시험 DB URL이 없는 선택 통합 시험이다. `ruff check .` 통과. A Adapter의 줄바꿈 1곳을 Ruff 형식으로 정리해 전체 `ruff format --check .`도 확인한다.
- 실제 MCP HTTP 서버와 Backend API를 로컬에서 실행했다. `backend_b_mcp`, JSON 기본 게임 데이터, 인메모리 Repository에서 정상·기획 수정·검증 조회 재시도·Primary Fallback·Agent 재시도·첫 기획용 조회 실패 6가지 API Trace를 다시 수집했다. `docs/team/backend-2/trace_samples/`의 Mock 4종과 실패 1종을 교체하고 Agent 재시도 1종을 추가했다. 행 수와 새 필드 확인 범위는 `frontend_trace_handoff.md` 최신 절에 있다.
- 실제 OpenAI 선택 시험 `backend/tests/test_backend_b_llm_live.py`는 Supervisor·Balance & Rule·Planner·Critic 모두 같은 실제 LLM 전송을 쓰도록 갱신했다. Supervisor `routing` 이유와 Balance & Rule 판정에 실제 LLM 표시가 있는지도 검사한다. 키가 아직 없어 실행 결과는 없으며 기본 시험에서는 skip이다. 과거 `normal_live_llm.json`과 `planning_error_live_llm.json`은 이전 두 Agent 중심 실행 기록이다.

### 남은 외부 연동 확인

1. 사용자가 `backend/.env`에 `OPENAI_API_KEY`를 준비한 뒤 시험 전용 `LLM_TEST_MODEL`을 명시하면 네 Agent 실제 LLM 선택 시험을 실행하고, 모델·결과·검증 범위를 이 문서에 기록한다. 시험은 명시적으로 켰을 때만 `backend/.env`의 키를 읽으며, 기존 셸 환경변수를 덮어쓰지 않는다. macOS: `LLM_TEST_MODEL=gpt-4.1-mini backend/.venv/bin/python -m pytest backend/tests/test_backend_b_llm_live.py -q -rs`. Windows PowerShell: `$env:LLM_TEST_MODEL="gpt-4.1-mini"` 설정 후 `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_backend_b_llm_live.py -q -rs`. `.env` 값과 키는 출력·커밋하지 않는다. API 전체 `backend_b_live` 재검증에는 추가로 MCP 서버와 `MCP_URL`이 필요하다.
2. **Backend A 담당자 요청:** 공유 DB를 임의로 초기화하지 않고, 격리된 시험용 PostgreSQL 주소와 `game_data_catalog` 적용 상태를 확인해 달라. 스키마와 초기 데이터는 A의 `backend/scripts/apply_game_data_catalog.py`로 A가 적용한다. 테스트 DB라면 `POSTGRES_TEST_URL`과 Backend용 `DATABASE_URL`을, MCP 담당자와 공유할 동일 DB의 읽기 연결 주소를 알려 달라. `backend/tests/test_postgres_integration.py`는 `reset()`을 실행하므로 공유 DB 주소로 실행하면 안 된다.
3. **MCP 담당자 요청:** 같은 시험 DB를 가리키는 `MCP_DATABASE_URL`로 서버를 구동하고 기획용 조회·ID별 조회의 `game_data_version`과 `source`가 DB 자료를 반영하는지 확인해 달라. B는 A·MCP 준비 후 `backend_b_mcp` API에서 기획 전 조회 → 재조회·검증 → Trace·복구 경로를 다시 검증한다.
4. Frontend 담당자에게 `frontend_trace_handoff.md` 최신 절과 현재 Mock API Trace 6종을 전달한다. 실제 LLM Trace 2종은 최신 필드가 없는 과거 자료로 표시한다.

### 격리된 PostgreSQL·최신 실제 LLM 검증 결과 (2026-10-08)

위의 LLM 키·PostgreSQL 미검증 설명은 이 시험 이전 기록이다. 이 컴퓨터에는 이 저장소와 관계없는 `/Users/son/개발/연습장/backend`의 `backend-db-1` 컨테이너가 이미 있었으며, 그 DB는 조회·수정하지 않았다. 별도의 일회용 `postgres:16-alpine` 컨테이너를 localhost 자동 포트로 띄워 `backend_b_test` 시험 DB를 만들었다. 공유 DB나 팀 환경의 데이터는 변경하지 않았다.

- A의 `python -m backend.scripts.apply_game_data_catalog`를 시험 DB에 적용해 `game_data_catalog`에 `tiny_heroes` 한 행(`tinyheroes-1.0.0`)을 넣었다. DB JSON은 저장소의 `mcp_server/data/tiny_heroes.json`과 일치한다. Primary와 Fallback Snapshot JSON도 현재 동일하다.
- A의 카탈로그·Repository PostgreSQL 선택 시험은 4 passed, MCP의 DB Primary·Fallback 독립성 시험은 1 passed였다.
- Mock LLM + 실제 MCP HTTP + 시험 PostgreSQL의 `backend_b_mcp` API에서 정상 요청(`WAITING_APPROVAL`, `primary`, Trace 19행)과 Primary 오류 주입(`WAITING_APPROVAL`, `fallback_snapshot`, Trace 21행)이 통과했다. Backend 재시작 뒤에도 정상 Task와 Trace 19행을 다시 조회했다.
- `backend/.env`의 키·모델을 값 노출 없이 사용해 네 Agent 실제 LLM 선택 시험 `backend/tests/test_backend_b_llm_live.py` 2건이 통과했다. 이 시험의 MCP 응답은 고정 자료다.
- 이어 `backend_b_live` API에서 실제 OpenAI + 실제 MCP HTTP + 시험 PostgreSQL로 정상 1일 요청 1건을 실행했다. `WAITING_APPROVAL`, Critic `PASS`, 규칙 검증 `true`, 게임 데이터 출처 `primary`, Trace 15행이었다. Supervisor의 정상 경로 결정 4행과 Balance & Rule 판정 이유에 실제 `LLM 판단:`이 기록됐다. 전체 Trace는 `trace_samples/normal_live_llm.json`에 저장했다. 실제 모델 응답 품질의 반복 측정은 아니며, `planning_error_live_llm.json`은 네 Agent 확장 전 기록이다.

시험 종료 후 로컬 API·MCP 서버와 일회용 PostgreSQL 컨테이너를 중지·제거했다. 이 시험은 **격리된 임시 DB**의 검증이며 공용 PostgreSQL 적용 상태를 증명하지 않는다. 공유 DB에서 재검증하려면 여전히 A에게 카탈로그 적용 상태와 검토된 연결 정보를 확인해야 한다. `.env` 값이나 시험 DB 주소는 커밋하지 않는다.
