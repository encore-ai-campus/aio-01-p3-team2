# 백엔드 B 장애 복구 반복 평가

기준: [팀 공통 Workflow 장애 복구 기록·평가 합의](../../workflow_evaluation_contract.md). 이 문서는 백엔드 B 평가 도구의 사용법과 확인된 결과를 기록한다.

## 평가 도구

`backend/evaluation/recovery.py`는 API Task 응답과 `GET /api/tasks/{task_id}/trace`를 비교한다. 정상, 기획 오류 후 수정, MCP 조회 재시도, Primary 오류 후 Fallback, 재시도 한도 초과 후 Safe Stop 다섯 시나리오의 기대 상태와 필수 Trace 순서를 정의했다. 추가 Agent 재실행이나 Tool 조회는 허용하되 필수 이벤트의 상대 순서와 Task별 `sequence`를 검사한다. Tool logs는 복구 판정에 사용하지 않는다.

`backend/scripts/evaluate_workflow_recovery.py`는 실행 중인 Backend API에 각 시나리오를 지정 횟수만큼 요청한다. 실행마다 새 `idempotency_key`를 사용하고 Test Mode 장애 주입 여부를 구분한다. 보고서에는 실행별 기대·실제 상태, 오류 유형, 복구 조치, 재검증 결과, 출처·버전, 복구 시간과 판정 항목, 시나리오별 집계가 들어간다. 오류가 관찰되지 않은 정상 요청은 자동 복구율 분모에서 제외한다. Safe Stop은 복구 성공과 별도로 집계한다.

macOS에서 Mock LLM·Mock MCP의 B 경로를 로컬 Backend API로 시험하는 예:

```bash
WORKFLOW_ENGINE=backend_b_mock ENABLE_FAULT_INJECTION=true backend/.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 실행한다:

```bash
backend/.venv/bin/python -m backend.scripts.evaluate_workflow_recovery --base-url http://127.0.0.1:8000 --mode backend_b_mock --runs 3 --output /tmp/backend_b_recovery_report.json
```

Windows PowerShell에서는 `WORKFLOW_ENGINE=backend_b_mock`와 `ENABLE_FAULT_INJECTION=true`를 각각 `$env:`로 설정하고 실행 파일을 `backend\.venv\Scripts\python.exe`로 바꾼다. 출력 경로는 Windows에서 쓰기 가능한 절대 경로로 지정한다. Test Mode를 켜는 것은 **격리된 시험 서버**로 제한한다. 실제 MCP를 사용할 때는 서버의 `WORKFLOW_ENGINE=backend_b_mcp`, `MCP_URL`을 먼저 설정한다. 실제 LLM을 반복 호출할 때는 서버에 `backend_b_live`와 실제 MCP·OpenAI 설정이 필요하고, 평가 명령에 `--mode backend_b_live --model <모델명> --allow-live-llm`을 명시한다. 이 경우 호출마다 API 요금이 발생한다. 보고서의 `--mode`·`--model`은 실행자가 서버 설정과 일치시켜야 한다.

`--scenario normal`처럼 한 사례만 선택할 수 있으며 옵션을 반복해 여러 사례를 고를 수 있다. 결과 파일이 이미 있으면 덮어쓰지 않는다. 종료 코드 0은 선택한 모든 실행의 기대 상태·Trace·지표 판정이 통과했다는 뜻이고, 1은 한 건 이상 실패했다는 뜻이다. JSON 파일에는 실제 `task_id`가 포함되므로 팀에 공유할 때는 환경·비밀값 노출 여부를 확인하고 필요한 집계만 문서에 옮긴다.

### PostgreSQL 재시작 보존 검증

전용 로컬 PostgreSQL을 Backend의 `DATABASE_URL`에 연결한 상태에서 위 평가 명령을 실행하고, Backend 서버를 종료한 뒤 **같은 DB URL**로 서버를 새로 시작한다. 다음 명령은 기존 JSON 보고서의 Task ID로 Task·Trace를 다시 조회하여 상태, 오류 유형, 복구 조치, 데이터 출처·버전, 필수 Trace 순서와 복구 시간 판정이 처음과 같은지 검사한다. 새 Task를 만들거나 DB 기록을 변경하지 않는다.

```bash
backend/.venv/bin/python -m backend.scripts.verify_workflow_recovery_report --base-url http://127.0.0.1:8000 --report /tmp/backend_b_recovery_report.json
```

Windows PowerShell에서는 Python 경로를 `backend\.venv\Scripts\python.exe`로, 보고서 경로를 Windows 절대 경로로 바꾼다. 재조회 명령의 종료 코드 0은 원본 보고서의 모든 실행이 재시작 후에도 동일하게 통과했다는 뜻이다. 원본 실행 실패, Task·Trace 조회 실패, 판정 차이는 종료 코드 1과 불일치 행으로 표시된다. 인메모리 저장 모드에서는 서버 재시작 후 Task가 사라지므로 이 절차가 통과할 수 없다. 생성 단계의 Test Mode는 격리된 로컬 서버에서만 사용하며 공용 DB를 장애 주입 시험 대상으로 사용하지 않는다.

자동 회귀 테스트 `backend/tests/test_backend_b_recovery_persistence.py`는 `POSTGRES_TEST_URL`이 지정된 경우에만 실행된다. 기존 DB 전체를 `reset()`하지 않고 시험이 생성한 Task만 삭제하지만, 주소에는 전용 로컬 시험 DB만 사용한다.

## 로컬 반복 평가 결과 (2026-10-09)

`youngmin`에서 `backend_b_mock` API를 로컬 HTTP로 실행하고 다섯 시나리오를 각각 3회 호출했다. **15/15건이 기대 상태·필수 Trace 순서·복구 시간 확인을 통과**했다. 실제 오류가 기록된 실행 12건 가운데 수정·Retry·Fallback으로 승인 대기에 도달한 실행은 9건으로 자동 복구율은 **9/12 = 75%**였다. 남은 3건은 의도적으로 재시도 한도를 초과한 사례였고 모두 후보 없이 Safe Stop해 안전 종료 성공률은 **3/3 = 100%**였다. 정상 사례 3건은 오류가 없어 복구율 분모에 넣지 않았다.

| 시나리오 | 반복 | 기대 결과 | 확인 결과 |
| --- | ---: | --- | --- |
| 정상 | 3 | 승인 대기, 복구 이벤트 없음 | 3/3 통과 |
| 기획 오류 후 수정 | 3 | 수정·재검증 후 승인 대기 | 3/3 통과 |
| MCP 조회 일시 Timeout | 3 | 같은 Tool 재시도 후 승인 대기 | 3/3 통과 |
| Primary Tool 오류 | 3 | Fallback 후 승인 대기 | 3/3 통과 |
| 재시도 한도 초과 | 3 | 승인 후보 없는 Safe Stop | 3/3 통과 |

복구 시간은 첫 오류와 재검증 성공 또는 Safe Stop의 **Trace 시각 차이**로 산출됐다. 이 Mock 실행은 같은 프로세스에서 매우 빠르게 처리되므로 측정값을 실제 LLM·MCP 운영 지연의 성능 지표로 해석하지 않는다. 이 결과는 반복 평가 도구와 결정적 복구 경로의 검증이며, 실제 LLM 품질의 반복 측정은 별도로 기록해야 한다. 일시 평가 JSON은 저장소 밖에 두고 실행별 Task ID는 이 문서에 적지 않았다.

## 실제 LLM 반복 평가 결과 (2026-10-09)

로컬 MCP HTTP 서버의 JSON 기본 게임 데이터(`tinyheroes-1.0.0`)와 `backend_b_live`의 `gpt-4.1-mini`를 사용했다. Backend 저장소는 인메모리로 두고 정상·기획 오류 주입 사례를 각각 3회 실행했다. Test Mode는 격리된 로컬 API에서만 켰다. 실행별 결과와 Trace를 평가 CLI가 비교했다.

| 시나리오 | 반복 | 기대 상태·복구 | 확인 결과 |
| --- | ---: | --- | --- |
| 정상 | 3 | `WAITING_APPROVAL`, 오류·AutoHealing 없음 | 3/3 통과, `source=primary` |
| 기획 오류 1회 주입 | 3 | `mission_unreachable` 감지 → Supervisor 수정 → 전체 재검증 → `WAITING_APPROVAL` | 3/3 통과, 오류 관찰 3건 모두 복구 |

두 사례를 합쳐 **6/6건 통과**했고, 실제 오류가 관찰된 실행 기준 자동 복구율은 **3/3 = 100%**였다. 기획 오류의 첫 실패 Trace부터 재검증 성공 Trace까지 각각 **11.581초, 5.914초, 6.234초**였다. 이 실행 묶음에는 안전 종료를 기대한 사례가 없어 안전 종료 성공률은 계산하지 않는다. 이 값은 소수의 고정 입력·모델 반복 결과이며 다른 대상·기간, 장시간 운영 안정성이나 일반적인 LLM 품질을 보장하지 않는다. 실제 MCP 연결은 사용했지만 Primary 게임 데이터는 JSON 기본 자료였고 PostgreSQL은 이 반복 평가에 사용하지 않았다. 임시 API·MCP 서버는 종료했다.

## 전용 PostgreSQL 복구 기록 재시작 검증 (2026-10-09)

별도 일회용 `postgres:16-alpine` 컨테이너에서 `backend_b_mock`의 정상, 기획 수정, Tool 재시도, Primary Fallback, Safe Stop을 각각 1회 실행했다. 생성 프로세스가 종료된 뒤 별도 API 프로세스에서 Task·Trace를 다시 조회해 기존 평가 결과와 비교했으며 **5/5건 동일하게 통과**했다. 검증은 Backend A의 PostgreSQL Repository와 백엔드 B의 복구 Trace·평가 계약을 함께 거친다. 이번 시험은 Mock LLM·Mock MCP를 사용했고 실제 MCP HTTP·OpenAI·Clash API의 재시작 보존이나 공용 DB 적용을 확인한 결과는 아니다. 시험용 Task를 삭제하고 일회용 컨테이너를 종료·제거했다.

## 실제 LLM·MCP·전용 PostgreSQL 복구 반복 평가 (2026-10-09)

각 시험 묶음마다 별도 일회용 `postgres:16-alpine` DB에 Backend A의 `tiny_heroes` 카탈로그를 적용하고, Backend의 `DATABASE_URL`과 MCP의 `MCP_DATABASE_URL`을 같은 DB로 설정했다. MCP HTTP 서버와 `backend_b_live` API에서 실제 OpenAI `gpt-4.1-mini`를 사용했다. Test Mode는 이 격리된 로컬 API에서만 켰다. 네 장애 시나리오를 각각 2회 실행한 뒤 Backend API 프로세스를 종료하고, 같은 DB에 연결한 새 API 프로세스에서 평가 보고서의 Task·Trace 판정을 다시 확인했다.

| 시나리오 | 실행 결과 | 첫 오류부터 재검증 또는 안전 종료까지의 Trace 시간 |
| --- | --- | --- |
| 기획 오류 후 수정 | 2/2 `WAITING_APPROVAL`, `mission_unreachable` 복구 | 8.704초, 6.881초 |
| MCP Timeout 재시도 | 2/2 `WAITING_APPROVAL`, `mcp_timeout` 복구 | 3.263초, 3.302초 |
| Primary Tool 오류 후 Fallback | 2/2 `WAITING_APPROVAL`, `mcp_tool_error` 복구, `fallback_snapshot` | 1.562초, 1.630초 |
| 재시도 한도 초과 | 2/2 `FAILED`, `agent_failure` 후 Safe Stop | 5.734초, 4.557초 |

**8/8건**이 기대 상태·필수 Trace 순서·복구 시간 판정을 통과했고, 재시작 후에도 **8/8건 동일 판정**이었다. 실제 오류가 관찰된 8건 중 6건이 자동 복구되어 승인 대기에 도달했다(6/8 = 75%). 나머지 2건은 의도한 안전 종료이며 2/2건 후보 없이 종료됐다. Primary를 사용한 성공 사례의 게임 데이터 버전은 `tinyheroes-1.0.0`이고, Fallback 사례는 `source=fallback_snapshot`으로 기록됐다. 정상 무장애 사례, 다른 입력·모델, 실제 Clash 공개 API, 장시간 안정성 및 공용 DB는 이번 반복 평가에 포함되지 않는다. 두 일회용 DB와 API·MCP 서버는 시험 후 종료·제거했고 원본 JSON은 저장소 밖의 임시 경로에 두었다.
