# 실제 LLM 시험 실행 준비

상태: **준비 계약·검증 이력 보존 / 실제 본 실험·Edge 완료** · 2026-10-11

후속 실제 결과: [19/19 본 실험 결과·39회 호출·UI 증빙](../검증산출물/2026-10-11_실제LLM_복구추가시험결과.md). 아래 사전 점검의 호출 0과 예상 비용은 당시 증빙/추정이며 실제 실험 기록과 구분한다.

참조: [19 시험 목적·순서](19_실제LLM_복구_추가시험계획.md), [17 정밀 시험 기준](17_오토힐링_정밀테스트_계획.md), [기존 137개 결과](../검증산출물/2026-10-11_단일오류_본시험결과.md), [현재 구현 상태](../현재_구현상태.md).

## 1. 실행 경계와 파일

| 파일 | 역할 |
|---|---|
| `infra/live-recovery-local.ps1` | 소유한 PostgreSQL 컨테이너의 설정으로 시험 DB 환경 구성. Python CLI 종료 후 부모 환경 복원 |
| `backend/scripts/live_recovery_experiment.py` | 기본 사전 점검, 명시적 실호출 실행, 정책 고정·전역 시험 lease·실제 Supervisor Workflow·증빙 저장 |
| `backend/tests/support/live_recovery.py` | 12개 사례 fixture, 지속 캠페인 예산 예약, 요청·산출물·보상 계산 독립 검증 |
| `backend/tests/support/mcp_fault_proxy.py` | 임시 loopback 포트의 실제 HTTP 장애. 본문·헤더를 증빙에 기록하지 않음 |
| `backend/tests/unit/test_live_recovery_preparation.py` | 준비 계약·예산·로컬 HTTP·산출물 변조 검출 검사. 유료 provider 호출 없음 |
| `frontend-web/src/fault-lab-topology.js` | CLI 실행 ID 조회, 실호출 사용량/최종 상태/검증 항목 표시 |

기존 시험실 API의 결정적 adapter 제한은 유지한다. 실호출은 CLI 프로세스 내부에서 기존 Planner/Critic의 실제 transport를 예산으로 감싸고, 기존 `submit`·Supervisor·정책·검증·암호화 저장을 사용한다. 새 상시 백엔드/프런트 서버는 만들지 않는다.

사용 DB는 **eventquest_fault_test**, MCP는 **8014**, 시험 UI/API는 **5183/8013**이다. PostgreSQL 컨테이너 소유권·DB 이름/계정·loopback 주소·CLEAN을 확인한다. 게임 승인/적용은 실행하지 않는다. 임시 MCP 프록시는 해당 CLI에서만 사용하고 종료한다. 사용자 서버·게임 DB·활성 정책은 변경하지 않는다.

## 2. 준비와 실행 명령

저장소 루트 PowerShell에서 실행한다. `backend/.env`를 읽되 명시적인 시험 환경변수는 덮어쓰지 않는다. 키·비밀번호·원문은 콘솔에 출력하지 않는다.

```powershell
# 실제 DB/MCP capability·snapshot·암호화·정책·예산 점검: LLM 호출 0
powershell -NoProfile -File infra/live-recovery-local.ps1

# 실제 MCP SDK에 임시 HTTP 장애를 주고 재조회: LLM 호출 0
powershell -NoProfile -File infra/live-recovery-local.ps1 -ProbeWire
```

실제 LLM 실행에는 아래 옵션이 모두 필요하다. 준비 과정에서는 실행하지 않았고, 이후 본 실험에서 명시적으로 실행했다.

```powershell
$campaignId = [guid]::NewGuid().ToString()
powershell -NoProfile -File infra/live-recovery-local.ps1 `
  -Execute -AllowLiveLlm -Case normal_single -CampaignId $campaignId
```

동일 시험 묶음에는 같은 `CampaignId`와 예산을 사용한다. 각 실행은 새 Workflow/시험 ID를 만들지만 캠페인 호출 예약은 누적된다. 과거 Workflow를 재개하지 않는다. 정상 3종 각 3회 → 일시 LLM 복구 → 한도 종료 대조 → 일시/지속 MCP 장애 → 새 정상 요청 → Edge 확인 순으로 진행한다. 실패/판정 불가/예산 소진 시 원인을 기록하고 후속 실행을 멈춘다.

## 3. 사례와 독립 기대값

| Case | 상황 | 기대 종료 |
|---|---|---|
| `normal_single` | 공간 1개·재화 보상 | WAITING_APPROVAL |
| `normal_multi` | 공간 2개·각 행동과 보상 연결 | WAITING_APPROVAL |
| `normal_unsupported` | 미등록 공간 capability | COMPLETED, 개발 필요 표시·Runtime 차단 |
| `planner_format_once` | 실제 응답 후 첫 Planner 업무 데이터의 형식 변형 | 재호출 후 WAITING_APPROVAL |
| `planner_explanation_once` | 첫 Critic 응답에 설명 수정 필요 상황 | 실제 재기획/재검수 후 WAITING_APPROVAL |
| `llm_timeout_once` | 첫 Planner 호출 전 timeout 예외 | 다음 실제 호출 후 WAITING_APPROVAL |
| `planner_format_always` | 모든 Planner 응답 형식 변형 | 허용 시도 한도 후 SAFE_STOP |
| `llm_timeout_always` | 모든 Planner 호출 전 timeout | 한도 후 SAFE_STOP, provider 호출 0 |
| `mcp_503_once` | 첫 HTTP POST 503 | 실제 MCP 재조회 후 WAITING_APPROVAL |
| `mcp_disconnect_once` | 첫 HTTP 연결 절단 | 실제 MCP 재조회 후 WAITING_APPROVAL |
| `mcp_delay_once` | 첫 HTTP 응답 6초 지연, 기존 호출 한도 5초 | 실제 MCP 재조회 후 WAITING_APPROVAL |
| `mcp_503_always` | 지속 HTTP 503 | MCP 한도 후 SAFE_STOP, provider 호출 0 |

LLM timeout 상황은 실제 OpenAI 서버 장애를 유발하는 시험이 아니다. 형식/설명 상황은 실제 응답 후 데이터 변형이다. MCP 503·절단·지연은 실제 HTTP/SDK 실패다. 현재 준비는 단일 원인 중심이며 복합 장애 전수 시험을 완료했다고 표현하지 않는다.

최종 상태만으로 PASS를 주지 않는다. 원래 목적/대상/기간/공간/행동/보상/연결 보존, EventPlan schema, 미션 매핑, 보상 총량의 직접 계산과 근거, 지원 범위, 실제 호출 사용량과 암호화 기록 수, 정책 규칙·상태 버전/전이·종료 후 실행 금지, 저장 결과·멱등 재조회 호출 0, 게임 데이터 보존을 함께 확인한다. 일시 복구는 실제 재호출 근거가 필요하다. 지속 장애의 종료는 정상화 성공률에서 제외한다.

## 4. 비용·토큰·시간 제한

- 기존 Workflow 한도: 호출 **8**, 전체 기한 **180초**, 실제 LLM 호출당 **30초**. `.env`의 누적 입력/출력 토큰·비용 한도를 유지한다.
- 현재 설정: 입력 **48,000**, 출력 **8,192** 토큰, Workflow 비용 **$0.05**. 모델 `gpt-4.1-mini`. 설정 단가와 가격 버전은 provider의 현재 청구 규격을 검증했다는 뜻이 아니다.
- 캠페인 기본: 사용자 $3 예산 허용에 따라 호출 **90회**, 설정 단가 기준 최대 예약 **$3.00**. 입력 예약 총량 **4,320,000**, 출력 예약 총량 **737,280** 토큰. CLI로 호출 수/비용을 낮출 수 있으며 비용 $3 초과 설정은 거절한다. 이전 $1/40회 준비 증빙은 당시 설정으로 보존한다.
- 실제 전송 전에 문맥·instructions·schema의 바이트 길이에 여유를 더한 보수적 입력 검사를 한다. 정확한 토큰 측정이라고 표현하지 않는다. 출력은 기존 provider 상한을 사용한다.
- 현재 호출당 최악 예약은 **$0.0323072**다. 성공해도 예약을 환급하지 않으며 90회 예약은 **$2.907648**로 $3 안에 들어간다. 전체 권장 시험은 첫 실행 전에 `-MaxCalls 90 -MaxCost 3.00`으로 정한다(현재 기본값과 동일). 실행 도중 자동으로 상향하지 않는다.
- SDK 숨은 재시도는 기존 `max_retries=0`을 유지한다. 실제 사용량과 설정 단가 계산 비용은 별도로 기록한다. timeout 등 사용량 불명도 예약을 유지하고 청구액 0으로 표시하지 않는다. 측정량이 예약 상한을 넘으면 캠페인을 차단한다.
- `campaign-<UUID>.json`에 전송 전 원자적으로 예약을 저장한다. 같은 ID의 모델/가격/한도가 달라지면 거절한다. 파일을 지워 한도를 우회하지 않는다.

## 5. 증빙·UI·정리

- 준비: `backend/test-results/fault-lab/live/preflight-<UUID>.json`.
- 실행: DB `fault_lab_records` 및 로컬 `run-<UUID>.json`. 실제 usage·원래 응답 원문은 기존 암호화 `llm_prompt_records`; 보고서에는 원문을 복사하지 않는다.
- 정책 version/hash·소스 파일 digest·fixture digest·DB/MCP snapshot·주입 journal·실제 Trace·규칙 판정·시간·provider attempts를 연결한다. 사용량 불명의 시도는 확인된 token/cost 합계와 구분한다.
- UI `http://127.0.0.1:5183` → **복구 시연 → CLI 개별 시험 기록 ID → 기록 ID로 조회**. `LIVE_RUN`의 UUID를 넣는다. 조회/새로고침은 GET만 수행한다. 실제 실행 경로는 별도로 펼친다.
- 처리 후 MCP URL/agent factory·부모 환경을 복원하고 임시 프록시를 닫는다. 정책과 게임 상태가 달라지면 DIRTY·INCONCLUSIVE로 다음 시험을 막는다. 강제 프로세스 종료로 BUSY가 남은 경우 자동 초기화하지 말고 기존 소유권/정리 절차로 확인한다.

## 6. 준비 검증 결과와 남은 실험

- 준비 및 영향 단위 검사 **88 passed**: 새 준비 계약, 기존 주입/oracle, Workflow LLM 예산. 실제 LLM 호출 없음. 단위 검사에서 개인 `.env` 로더를 교체해 사용자 환경을 읽지 않는다.
- Edge **2 passed**: 실제 저장 결과 ID 조회/새로고침의 POST 0, 실호출 보고서 표시. 두 번째는 명시적 UI fixture 검사이며 실제 LLM 성공 증빙이 아니다.
- 실제 MCP capability/snapshot 조회와 4종 HTTP 경계 점검 통과: 일시 503/절단/지연의 첫 실패·다음 성공, 지속 503의 두 번 실패. 이 점검은 준비 코드의 재조회이며 Supervisor 복구 성공 증빙은 실제 실험에서 수집한다.
- 원래 정책/CLEAN/게임 상태 복원 확인. Ruff check/format, frontend build 통과.
- 최종 원본 준비 증빙: `backend/test-results/fault-lab/live/preflight-ad7aeff8-a2df-4dd3-9ca3-4624007de184.json`. `PREPARED_NOT_EXECUTED`, `provider_calls=0`, 통신 경계 4/4, `cleanup_ok=true`를 확인했다. 로컬 증빙은 기존 정책에 따라 Git에서 제외한다.
- 최초 로컬 HTTP 준비 검사에서 Windows의 503 연결 reset을 발견해 요청 body 소비 후 응답하도록 수정했다. 3개 산출물 검사의 영향 상태 문자열을 실제 계약의 `calculated`에 맞춰 수정하고 재검사했다. 시험 입력/기대 계약 수정이며 게임 밸런스·정책을 완화하지 않았다.
- 형식 주입은 provider usage/암호화 저장용 메타데이터를 보존하도록 수정했다. 실제 응답의 증빙을 지워 예산 오류로 바뀌는 일을 막으며, 변형된 업무 응답은 기존 형식 검증이 처리한다.
- 기존 snapshot 조회에 SQLAlchemy 순환 FK 정렬 경고 1종이 남아 있다. 읽기 증빙 점검은 통과했으며 DDL 정렬·마이그레이션 문제까지 해결했다고 표현하지 않는다.

**준비 당시 확인하지 않은 것:** API 키의 실제 인증·설정 단가의 정확성·실제 생성/재호출/기획 수정 품질·실호출 결과를 이용한 Edge 시연. 이후 공식 단가와 실제 호출/복구/Edge를 확인했다. [별도 결과보고서](../검증산출물/2026-10-11_실제LLM_복구추가시험결과.md)에 실제 사용량과 평가 범위를 기록한다. 계정 청구 확정액이나 모든 생성 응답의 품질을 검증했다는 뜻은 아니다. 기존 137 PASS와 합산하지 않는다.

## 7. 실험 토큰·비용 사전 추정 (2026-10-11 후속 확인)

공식 [GPT-4.1 mini 가격](https://developers.openai.com/api/docs/models/gpt-4.1-mini)은 일반 입력 $0.40/100만 토큰, 출력 $1.60/100만 토큰으로 현재 설정 단가와 일치함을 확인했다. 위 준비 증빙 당시의 미확인 상태는 보존한다. API 인증·실제 usage·계정 청구액은 아직 확인하지 않았다. 아래 계산은 캐시 할인과 세금을 제외한다.

실제 시험 API의 활성 정책은 Planner 형식/LLM timeout/MCP 통신의 최대 시도 4, 설명 재기획 최대 시도 3이다. 예상대로 정상화되는 경우 호출 계산은 다음과 같다.

| 권장 실행 범위 | 예상 유료 호출 |
|---|---:|
| 정상 3종 각 3회 | 18 |
| 일시 LLM 형식/설명/timeout 각 1회 | 3 + 4 + 2 = 9 |
| 지속 형식/timeout 대조 | 4 + 0 = 4 |
| 일시 MCP 3종/지속 MCP | 6 + 0 = 6 |
| 장애 후 새 정상 요청 | 2 |
| 합계 | **39** |

UI의 결과 조회/새로고침은 추가 호출 0이다. timeout 상황 주입은 transport 이전이므로 provider 시도로 집계하지 않는다. 추가적인 모델 형식 오류·Critic 수정 요구·실제 API 장애가 생기면 호출 수가 늘거나 중단될 수 있다.

로컬 메모리의 결정적 adapter로 실제 전달 문맥의 크기만 조사했다(DB/MCP/provider 호출 없음). 정상 3종의 instructions/schema/문맥 합계는 Planner 약 17,200~18,400자, Critic 약 6,200~9,000자였다. 이는 토큰 실측이 아니며 결정적 응답의 길이를 실제 LLM 출력량으로 사용하지 않는다.

계획용 가정은 호출당 입력 **4,000~8,000**, 출력 **300~1,000** 토큰이다. 39회라면 입력 **156,000~312,000**, 출력 **11,700~39,000**, 합계 **167,700~351,000** 토큰이며 비용은 **$0.08112~$0.18720**다. 추가 재시도 여유를 둔 예상 지출 범위는 **$0.20~$0.50**로 본다. 실제 token usage가 나오면 이 가정을 갱신한다.

최초 추정에서는 60회/$2 상한을 권장했다. 이후 사용자가 $3까지 허용하여 **90회/$3**을 CLI·예산 관리·실행 스크립트의 기본값으로 반영했다. 예상 39회 대비 51회 여유가 있으며, 90회의 보수적 예약은 $2.907648다. 예상 지출 $0.20~$0.50과 예약/중단 상한 $3은 구분한다. 기존 Workflow $0.05 한도는 유지한다. 예산 변경 당시 실험은 미실행이었고 기존 원장이나 준비 증빙을 변경하지 않았다. 후속 실제 실험은 새 캠페인으로 실행해 결과보고서에 기록했다.

변경 후 설정 점검 원본은 `backend/test-results/fault-lab/live/preflight-ece24af6-b388-4793-8650-256852916b0e.json`이다. 90회/$3·provider 호출 0·`cleanup_ok=true`를 확인했고 해당 Python 파일의 Ruff check/format을 통과했다. 이 점검은 새 유료 실험이나 단위 시험 재실행이 아니다.
