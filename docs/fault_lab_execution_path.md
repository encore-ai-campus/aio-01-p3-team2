# 복구 시연 실행 경로 계약

`frontend-web`의 복구 시연은 격리 시험 API의 완료 결과를 조회해 재생한다. 시험 실행 중에는 실제 단계 진행률을 추정하지 않는다.

## 기록과 저장

Canonical Workflow의 Agent/MCP 복구 상세 이벤트는 복구 결정 또는 복구 성공이 발생한 시점에 `record_observation()`으로 기록한다. 최종 저장의 `finalize_execution()`은 실행 중 기록의 순서를 보존하고 결과 설명을 결합한다. 복구 상세를 완료된 승인 대기 기록 뒤에 새로 붙이지 않는다.

격리 시험의 주입·검출 journal과 이미 기록된 실행 이벤트는 같은 runner의 callback으로 수집한다. `journal_sequence`는 callback 호출 순서다. 실행 경로는 이 순서를 사용하며, 최종 Workflow Trace와 주입 시간을 임의로 섞어 새 순서를 계산하지 않는다. 검증용 `trace`, `injection_journal`, `detection_journal`은 기존 용도로 유지한다.

## 시험 결과의 추가 필드

Canonical 시험 결과의 `results[]`에는 다음 필드가 저장된다.

| 필드 | 의미 |
| --- | --- |
| `path_schema_version` | 현재 `1` |
| `path_topology` | 해당 시험의 노드, 오류 위치, 좌표, 연결선과 버전을 보존한 snapshot |
| `execution_path[]` | 주입·검출·실행·안전 종료 관측을 발생 순서대로 나열 |
| `execution_path[].sequence` | 화면 재생 순서. 재시도와 같은 노드 반복 방문도 유지 |
| `kind` | `injection`, `detection`, `execution`, `state_transition` |
| `node_id` | snapshot에 대응하는 노드. 대응할 수 없으면 `null` |
| `event`, `status`, `error_code` | 관측한 실행 사건, 상태, 오류 코드 |
| `target_id`, `occurrence_id`, `call_index` | 주입·검출 위치와 호출 식별자. 관측에 제공된 경우 표시 |
| `occurred_at`, `elapsed_monotonic_seconds` | runner가 관측한 시각과 경과 시간. Agent 처리 시간과 구분 |
| `phase`, `validation_round` | 규칙 검증 기록의 실제 방문 횟수. 첫 검증은 `validation`, 이후 검증은 `revalidation` |

주입 기록은 선택한 위치에서 `applied=true`인 호출만 경로에 포함한다. 실제 오류 검출은 별도 사건이다. 복구 결정은 Supervisor에, Agent 복구 완료는 해당 Agent에 대응한다.

## 화면 동작

실행 경로 보기는 결과에 저장된 `path_topology`와 `execution_path`를 사용한다. 이후 카탈로그가 바뀌어도 저장된 시험의 노드 대응과 버전은 유지된다. 주입 위치와 검출 위치를 별도 색상으로 표시하고 순서 목록을 그래프와 함께 제공한다.

기존 토폴로지에 없는 관측 사건 사이의 순서는 추가 연결선으로 표시한다. 이 선은 실행 기록의 인접 순서를 뜻하며 새로운 Workflow 호출 경로를 정의하지 않는다. 대응할 수 없는 사건은 목록에 남기고 그 구간의 연결선을 생략한다. 같은 노드를 여러 차례 방문한 기록은 합치지 않는다.

요청 전송 중에는 실행 버튼을 비활성화한다. 폴링은 요청한 run ID를 고정하고 이전 조회의 늦은 응답이 현재 시험 결과를 덮어쓰지 못하도록 확인한다. 재접속은 저장된 run ID를 조회하며 시험을 재실행하지 않는다.

추가 경로 계약이 없는 과거 결과와 기존 Task 경로는 Trace를 노드에 대응해 표시한다. 이때 주입 시점과 인과 순서를 보장하는 기록이 없다는 안내를 표시한다. 과거 결과를 새 관측 기록으로 소급 변환하지 않는다.

## 확인 명령

저장소 루트에서:

```powershell
python -m pytest backend/tests/unit/test_fault_execution_path.py backend/tests/unit/test_workflow_execution.py -q
node --test frontend-web/tests/fault-lab-path.test.js
```

`frontend-web`에서:

```powershell
pnpm install --frozen-lockfile
pnpm test:path
pnpm build
```

Python 검증은 결정적 Agent와 실제 주입 hook을 사용해 1회·연속 주입과 안전 종료, 저장 후 조회를 확인한다. 브라우저 검증은 API 응답 fixture를 사용해 폴링·반복 방문·고정 토폴로지·데스크톱·모바일 재조회를 확인한다. 이 브라우저 검증은 실제 시험 DB/MCP 또는 유료 LLM 실행 검증을 대신하지 않는다.

실제 PostgreSQL·HTTP MCP와 웹 화면을 함께 실행한 검증은 [실제 시험 환경 검증 기록](team/frontend/recovery-demo-validation.md)에 정리한다.
## 저장 완료 관측 보완 (2026-10-12)

카탈로그 `fault-lab-v1.2`부터 `storage.result_persisted`를 `store` 노드에 대응한다. 기획 결과의 Trace에 저장 사건을 포함하고, 결과 트랜잭션이 성공적으로 커밋된 후 시험 journal에 저장 완료를 기록한다. 실패한 저장 시도에는 완료 사건을 발생시키지 않는다. 계획이 없는 안전 종료 결과는 `result_kind=workflow_and_trace`, 계획이 있는 결과는 `plan_and_trace`로 구분한다.

`fault-lab-v1.2`에서는 승인 대기 기록 뒤에 저장 완료가 나타났다. `fault-lab-v1.3`부터 정상 기획의 순서는 **최종 일관성 확인 → 기획 결과·Trace 저장 완료 → Supervisor 승인 대기 전이 → 운영자 승인 대기**다. 결과 INSERT가 flush에 성공한 뒤 승인 대기를 기록하고, 최종 결과와 WorkflowOperation 상태를 같은 트랜잭션에서 커밋한다. journal은 커밋 성공 후 저장, 전이, 승인 대기 순서로 관측을 공개한다. flush 또는 커밋이 실패하면 승인 대기를 공개하지 않으며, 기존 상태 버전·소유자·취소 검사를 유지한다. 안전 종료는 승인 대기 없이 종료 결과와 Trace를 저장한다.

승인 대기 전이와 최종 결과를 함께 커밋하므로 외부 조회에서는 저장만 완료되고 승인이 누락된 중간 상태가 나타나지 않는다. JSON payload는 복사해 저장하며, 전이 후 payload와 digest가 함께 갱신돼 중복 제출과 재조회가 같은 결과를 반환한다.

저장된 과거 보고서는 불변이며 사건을 소급 생성하거나 재정렬하지 않는다. 화면을 새로고침하고 새 시험을 실행해야 새 순서를 볼 수 있다.

v1.2 확인 기록: 실제 LAN UI에서 `llm_timeout/once/current_active` 시험 `2b2f248c-a576-4e79-8a25-21e87a8d60f6`가 COMPLETED/PASS로 완료됐고 저장 노드 관측을 확인했다. v1.3의 저장·승인 순서 검증은 팀별 검증 기록의 후속 항목에 정리한다.

