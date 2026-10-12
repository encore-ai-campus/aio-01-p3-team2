# 복구 시연 UI 실제 시험 환경 검증

## 후속 검증: 저장 이후 승인 대기 (2026-10-12)

카탈로그 `fault-lab-v1.3`에서 최종 검증 이후 결과 INSERT를 먼저 flush하고, 승인 대기 전이와 결과·Trace·WorkflowOperation을 함께 커밋한다. 실패한 INSERT에는 승인 대기 기록이 없으며, 전이 단계 실패에는 저장도 롤백된다. 커밋 성공 이후 journal 공개 순서는 저장 완료 → Supervisor 전이 → 운영자 승인 대기다. 기존 결과는 변경하지 않는다.

관련 Python 검사 149개가 통과했다. SQLite 트랜잭션의 INSERT 실패·전이 실패 롤백, 저장 payload/digest 재조회 일치와 진행 상태에 조기 승인 대기가 없는 것도 포함한다. Ruff 검사에서는 원래 브랜치의 canonical_planning.py에 있던 B023 세 건과 TRY004 한 건이 남아 있고, 이번 수정 구간의 추가 위반은 없었다. 수정한 Python 파일의 포맷 검사와 diff 공백 검사는 통과했다.

첫 실제 검증에서는 JSON 객체를 공유해 전이 뒤 payload 변경을 감지하지 못하는 문제가 드러났다. payload를 복사하고 digest와 함께 갱신하도록 수정한 뒤 아래 실제 시험을 모두 다시 실행했다. 실패한 첫 검증 이력은 삭제하지 않았다.

| 오류 주입 | 종료 상태 | 결과 | Run ID |
| --- | --- | --- | --- |
| Planner 응답 오류 1회 | WAITING_APPROVAL | PASS | `1ff0330b-9656-47bc-9037-3124dbe73d54` |
| 기획 설명 불일치 1회 | WAITING_APPROVAL | PASS | `91901dda-5288-4852-9fc4-bbe796a21927` |
| MCP timeout 1회 | WAITING_APPROVAL | PASS | `37e41bf4-356d-4e83-a71b-7ba7871225bf` |
| MCP 규칙 조회 오류 1회 | WAITING_APPROVAL | PASS | `788f0d52-5f6a-4dec-8463-81c56db93486` |
| Planner 오류 반복 | SAFE_STOP | PASS | `ee6418ee-bfb9-4ec7-b19e-a3496f091ef9` |
| LAN UI LLM timeout 1회, 현재 활성 정책 | WAITING_APPROVAL | PASS | `09174f94-6e2b-4a27-9237-1bcf461802f2` |

API mock 없이 PostgreSQL/HTTP MCP와 결정적 Agent를 사용했다. 화면 목록·노드·연결선을 실제 journal 경로와 대조하고 재조회·중복 실행 방지와 모바일 화면을 확인했다. 수정·재검증 결과의 35단계 애니메이션 순서도 통과했다. 증거는 `frontend-web/test-results/fault-path-store-before-approval-final/`, `frontend-web/test-results/fault-path-store-approval-animation/`에 있다.

최종 LAN 시험의 마지막 사건은 22 최종 일관성 확인 → 23 저장 완료 → 24 Supervisor 승인 대기 전이 → 25 운영자 승인 대기였다. 정책 버전 1과 DB CLEAN을 확인했고 API 8183/MCP 8184/UI 5184 서버를 재시작해 실행 중으로 유지했다. 브라우저를 새로고침하고 새 시험을 실행하면 새 순서가 반영된다.

## 최초 검증 기록

검증일: 2026-10-12 (한국 시간)

작업 브랜치: `fix/recovery-ui-sync`, 기반 `origin/ik_yejin_front`의 `0ce40ea`.

실제 격리 PostgreSQL과 HTTP MCP, 별도 시험 runner를 사용했다. 웹 화면에서 위치·오류·패턴·정책을 선택하고 실행 버튼으로 요청했다. API 응답을 mock하지 않았다. Agent는 기본 결정적 모드이며 각 결과의 외부 LLM 호출 수는 0이다.

## 환경

| 대상 | 설정 |
| --- | --- |
| PostgreSQL | 소유권이 확인된 `ikonly-test-postgres-1`, `127.0.0.1:5434`, `eventquest_fault_test` |
| 시험 API | `http://127.0.0.1:8183` |
| 실제 MCP | `http://127.0.0.1:8184/mcp` |
| 검증 UI | `http://127.0.0.1:5184` |
| 시험 정책 | `retry_allowed`로 시험 후 기존 활성 버전 1로 복원 |
| 카탈로그 | `fault-lab-v1.1`, 실행 결과에 토폴로지 snapshot 저장 |

기존 시험 이력을 삭제하지 않았다. 각 시험은 DB의 환경 lease를 사용했고 완료 후 `CLEAN`, 소유자 없음, 활성 정책 버전 1을 확인했다. 검증을 위해 시작한 API·MCP 프로세스는 확인 종료 후 정리했다.

## 최종 실제 실행 결과

| 시나리오 | 실제 대응 | 종료 상태 | 경로 사건 | 검증 항목 | Run ID |
| --- | --- | --- | ---: | ---: | --- |
| `planner_output_invalid`, 1회 | Planner 재시도 1회 | `WAITING_APPROVAL` | 24 | 20/20 PASS | `09195815-52a3-4e36-a25e-01b7ac5e5bfb` |
| `planner_explanation_mismatch`, 1회 | 기획 수정 1회, 규칙 재검증 | `WAITING_APPROVAL` | 34 | 20/20 PASS | `1f3b953b-d3ac-462a-9fe3-7e67c6dc6c3a` |
| `mcp_timeout`, 1회 | 실제 MCP 재조회 1회 | `WAITING_APPROVAL` | 23 | 20/20 PASS | `95dfc3bb-8e99-4481-af22-6897ca658e07` |
| `mcp_game_rules_error`, 1회 | 대체 게임 규칙 조회 1회 | `WAITING_APPROVAL` | 23 | 21/21 PASS | `1c3e5c56-b210-43a5-9542-1d6014fa85dd` |
| `planner_output_invalid`, 계속 | 재시도 3회 후 안전 종료 | `SAFE_STOP` | 27 | 20/20 PASS | `f46fe208-be42-4787-876d-8b64a418385b` |

5개 실제 시나리오 모두 PASS였다. 안전 종료는 기대한 종료 동작으로 PASS이며 자동 복구 성공으로 표시하지 않는다.

화면의 순서 목록을 API의 `execution_path`와 항목별로 대조했다. 주입 전에는 검출 표시가 없고, 실제 검출 이후 오류 위치가 표시된다. 반복 노드 방문과 연결선이 결과에 대응하며, 기획 수정 뒤의 두 번째 규칙 검증은 재검증으로 표시된다. 대응되지 않은 경로 사건과 Trace 매핑 경고는 0개였다.

각 결과의 게임 Runtime 변경 없음, 금지된 Runtime 호출 없음, 정책 복원 검사도 통과했다. 데스크톱과 390px 모바일에서 화면 오류와 페이지 전체 가로 넘침이 없었고, 재접속 때 추가 시험 요청 없이 같은 경로를 읽었다. 모바일의 넓은 그래프는 기존대로 그래프 내부에서 가로 탐색한다.

## 애니메이션 대조

수정·재검증 시험의 저장된 실제 결과를 애니메이션으로 다시 재생했다. 브라우저 MutationObserver로 현재 강조된 순서와 노드를 수집해 백엔드의 34개 관측 사건과 대조했고 모두 일치했다. 주입 색상은 주입 사건부터, 검출 색상은 검출 사건부터 표시됐으며 재생 중 POST 요청은 0개였다.

## 검증 중 보완

두 번째 규칙 검증의 문구가 첫 검증과 같아 실제 재검증임을 구별하기 어려웠다. 실제 검증 기록의 방문 횟수로 `validation_round`와 `phase`를 부여하고 두 번째부터 재검증 문구를 표시했다. 재생 중 현재 사건을 목록 내부에서 따라가도록 했고 동작 줄이기 설정에서는 첫 주입 행을 바로 볼 수 있도록 했다.

정상 입력 접수 `brief.accepted`의 Trace 노드 대응이 카탈로그에 없어 경고가 나타났다. Supervisor의 입력 접수 사건으로 등록하고 카탈로그 버전을 올렸다. 수정 후 실제 시나리오 5개를 모두 다시 실행해 위 결과를 확인했다.

## 증거 및 재현

Git에 포함하지 않는 로컬 증거 위치:

- `frontend-web/test-results/fault-path-real-final/`: 각 시험의 `actual-report.json`, 데스크톱·모바일 PNG
- `frontend-web/test-results/fault-path-animation/`: `animation-observations.json`, 완료 화면 PNG
- `backend/test-results/fault-lab/`: 시험 runner journal과 서비스 로그

표준 시험용 Python 환경 `backend/.venv`를 준비한 뒤 저장소 루트에서:

```powershell
./infra/fault-lab-local.ps1 -ApiPort 8183 -McpPort 8184 -UiPort 5184
```

`frontend-web`에서 실제 시험을 실행한다. DB/MCP가 연결된 격리 환경이어야 한다.

```powershell
pnpm test:path:real
```

기록 재생 애니메이션만 확인하려면 실제 완료된 Run ID를 지정한다.

```powershell
$env:EVENTQUEST_PATH_SAVED_RUN_ID = '1f3b953b-d3ac-462a-9fe3-7e67c6dc6c3a'
$env:EVENTQUEST_REAL_PATH_OUTPUT_DIR = './test-results/fault-path-animation'
pnpm test:path:real --grep 'saved real recovery animation'
```

확인 완료 후 저장소 루트에서 `./infra/fault-lab-local.ps1 -Stop`을 실행한다. 환경이 `BUSY` 또는 `DIRTY`이면 스크립트가 종료를 차단하므로 소유자와 원인을 먼저 확인한다.
