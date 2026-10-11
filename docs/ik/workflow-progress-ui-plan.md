# Workflow 진행 기록 UI 변경 계획 및 구현 결과

## 상태와 범위

- 상태: 연결 그래프 구현 완료, 프런트엔드 브라우저 검사 완료
- 작업 브랜치: `ikonly`
- 범위: 기존 응답을 사용하는 운영 콘솔의 Workflow 진행 기록 화면
- 구현: `frontend-web/src/workflow-progress.js`, `frontend-web/src/main.js`, `frontend-web/src/styles.css`
- 확인: `frontend-web/e2e/workflow-progress.spec.js`, `frontend-web/playwright.workflow.config.js`

DB schema, 저장 Trace, API 요청·응답 계약, 백엔드 실행 경로는 이번 작업에서 변경하지 않았다. 처리가 끝난 뒤 받은 기존 결과를 표시하며 실시간 전송은 추가하지 않는다.

## 1. 화면 구성

하나의 Workflow 카드 안에서 주 흐름을 세로로 유지하고, 검증·평가와 Supervisor·복구 경로를 오른쪽에 배치한다. 별개의 목록을 나열하는 대신 실제 경로를 화살표로 연결한다.

| 레인 | 표시 대상 |
|---|---|
| 주 흐름 | 요청 접수, Planner 기획안 생성, 저장된 기획안 결과, 승인 대기·결정, Runtime 등록, 사람 검토 대기, 안전 종료 |
| 검증·평가 | 게임 근거 조회, 지원 범위·규칙 검증, Critic 평가, 최종 일관성 등 검토 이벤트 |
| Supervisor·복구 | Agent handoff, 복구 결정, 재시도·대체 조회·복구 완료 |

Supervisor는 주 흐름에 포함하지 않는다. Planner 또는 Critic으로 담당 단계를 전달하는 handoff는 대상 노드와 같은 높이에 두고 수평 화살표로 연결한다.

- 초록 화살표: 기록된 실행 순서, 검토 경로로 분기하거나 주 흐름으로 합류
- 파랑 화살표: Supervisor 전달·복구 경로
- 빨강 화살표: 안전 종료로 이어지는 경로
- 흐린 점선: 주 흐름의 기준 축. 검토 단계를 건너뛴 실행을 뜻하지 않는다.

Plan finding과 Critic 요약은 같은 Workflow 카드 아래쪽에 표시한다. 원본 Trace와 실행 metadata는 펼침 영역에서 확인한다. 기존 기획안 결과, 승인·거절 동작 및 Runtime 진행 패널은 이어서 표시한다.

## 2. 결과별 구성

화면은 특정 성공 결과에 고정하지 않고 실제 이벤트와 기존 저장 결과로 구성한다.

| 결과 | 구성 원칙 |
|---|---|
| 성공·승인 대기 | 요청 → 근거 조회 → Planner → 검증·Critic → 기획안 결과 → 기록된 승인 대기 |
| Critic REVIEW | Critic 의견과 검토 상태를 표시하고 주 흐름의 사람 검토 대기에서 종료 |
| 검증 차단 | 실제 차단된 단계와 기획안 결과를 표시. 없는 승인·등록 단계를 추가하지 않음 |
| 복구 성공 | 기록된 오류·복구 결정·재시도·후속 단계 순서대로 연결 |
| 안전 종료 | 기록된 복구 경로에서 주 흐름의 안전 종료로 연결 |
| 승인·거절 | 저장된 Decision 또는 승인 결정 Trace를 표시 |
| Runtime 등록 | 등록 Trace 또는 기존 Runtime record를 표시 |
| 과거·불완전 기록 | 존재하는 이벤트와 결과만 표시. 알 수 없는 이벤트도 원래 이름으로 보존 |

반복 실행은 Trace의 각 발생을 별도 노드로 유지한다. Planner 버전이나 재시도 횟수를 임의로 만들지 않는다.

## 3. 기존 데이터 사용 규칙

`buildWorkflowGraph()`는 현재 응답을 읽어 화면 전용 노드와 연결선을 만든다. 서버로 새 데이터를 전송하거나 이 그래프를 저장하지 않는다.

1. Trace 배열의 기록 순서를 유지하며 기존 sequence를 카드에 표시한다.
2. `llm.usage_recorded`는 그래프에서만 생략한다. 원본 상세 Trace에는 남긴다.
3. Trace 노드는 `Trace N`으로, 기존 Plan·Decision·Runtime·Workflow 상태에서 가져온 노드는 `저장된 결과`로 구별한다.
4. Plan 결과는 기존 승인·등록·안전 종료 이벤트 앞에 배치한다.
5. 승인 결정·Runtime 등록 Trace가 없더라도 기존 record가 있으면 해당 저장 결과를 표시한다. 같은 이벤트가 있으면 중복 생성하지 않는다.
6. 현재 Workflow 상태가 `WAITING_REVIEW`이면 사람 검토 대기를 표시한다. Workflow 상태가 없을 때만 Critic REVIEW를 보조 단서로 사용한다.
7. `SAFE_STOP`·`FAILED` 상태에 종료 Trace가 없으면 기존 상태에 근거한 종료 결과를 표시한다.
8. 카드 문구와 상세 JSON은 기존 응답에 있는 정보만 사용한다.

화살표는 같은 Workflow의 기록된 처리 순서를 나타낸다. 개별 finding이 특정 복구 결정을 유발했다는 직접 인과관계를 보장하지 않는다. finding별 원인·수정 연결이나 과거 버전별 상세 이력은 현재 데이터에 명시되어 있지 않으면 추정하지 않는다.

## 4. 좁은 화면

모바일에서도 같은 연결 그래프를 유지한다. 그래프 영역 내부를 좌우·상하로 스크롤하며 주 흐름과 옆 경로를 확인한다. 그래프 밖에 스크롤 안내를 표시하고 페이지 전체가 가로로 넘치지 않도록 한다. 요약 카드와 결과 패널은 세로로 배치한다.

## 5. 확인 결과

전용 Playwright 설정은 Vite만 실행하며 API 응답을 fixture로 대체한다. 실제 백엔드·DB·LLM·MCP 통합 실행을 확인한 결과는 아니다.

- 브라우저 검사 11건 통과: 성공, REVIEW, 검증 차단, 복구 성공, 안전 종료, 승인, 거절, Runtime 등록, Runtime Trace 누락, 과거 기록, 알 수 없는 이벤트·불완전 기록
- 하나의 Workflow 카드, 노드·화살표 수, Supervisor의 레인 분리, 분기·합류, 결과별 종료 지점 확인
- 브라우저 오류, 카드 내용의 세로 잘림, 390px 화면의 페이지 가로 넘침 검사
- 그래프 내부의 가로 이동 확인
- 프런트엔드 프로덕션 빌드 통과

실행 명령 (`frontend-web/` 기준):

```powershell
node node_modules/@playwright/test/cli.js test --config=playwright.workflow.config.js
node node_modules/vite/bin/vite.js build
```

실제 렌더링 캡처:

- [성공 및 승인 대기](ux-evidence/workflow-graph-success-desktop.png)
- [Critic REVIEW](ux-evidence/workflow-graph-review-desktop.png)
- [복구 경로](ux-evidence/workflow-graph-recovery-desktop.png)
- [안전 종료](ux-evidence/workflow-graph-safe-stop-desktop.png)
- [모바일](ux-evidence/workflow-graph-success-mobile.png)

## 6. 완료 기준과 제한

- 한 카드에서 주 흐름과 옆 경로의 실제 분기·합류·종료를 확인할 수 있다.
- Supervisor와 검증·평가가 주 흐름과 역할별로 구분된다.
- 저장된 상태에 따라 화면을 구성하고 존재하지 않는 후속 실행을 완료된 단계로 표시하지 않는다.
- 모든 원본 Trace는 상세 영역에서 확인할 수 있다.
- DB·API 계약과 백엔드 동작 변경이 없다.

실시간 위치 갱신, 정확한 퍼센트 진행률, 실행 취소·일시정지, finding별 복구 인과관계 추정은 포함하지 않는다. 긴 실행은 그래프가 세로로 길어질 수 있다.
