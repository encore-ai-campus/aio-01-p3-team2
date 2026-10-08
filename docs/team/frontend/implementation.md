# 프론트엔드 구현 현황

현재 `frontend/`는 Streamlit 기반 데모 로그인과 이벤트 생성, 결과·승인, 운영·디버그 화면을 제공합니다. 화면은 제공된 시안의 레이아웃을 참고해 Streamlit 컴포넌트와 HTML/CSS로 구성했습니다. Backend 구현은 이 작업에 포함하지 않았습니다.

사이드바는 흰색 로고 영역에 `frontend/assets/sidebar_logo.png`의 첨부 로고를 사용합니다. 로그인 화면의 로고 자산은 별도로 유지합니다.

## 실행과 설정

저장소 루트에서 다음 명령을 실행합니다.

```powershell
pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```

`frontend/.env.example`에 환경변수 이름을 정리했습니다. 앱은 `.env` 파일을 자동으로 읽지 않으므로 실행 전에 셸 또는 실행 환경에 설정해야 합니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `DEMO_ALLOWED_IDS` | `liveops01` | 데모 로그인에 허용할 ID 목록(쉼표 구분) |
| `BACKEND_BASE_URL` | `http://127.0.0.1:8000` | Streamlit 서버에서 호출할 Backend 주소 |
| `SHOW_TEST_MODE` | `false` | 장애 주입 UI 표시 여부. Backend의 별도 허용 설정을 대신하지 않음 |

컨테이너 실행용 파일은 `frontend/Dockerfile`, `frontend/.env.docker.example`에 있습니다. 컨테이너 네트워크에서 Backend 서비스 이름이 `backend`일 때 예시 주소는 `http://backend:8000`입니다. 실제 서비스 이름과 포트에 맞춰 변경해야 합니다.

Docker를 사용하는 환경에서는 저장소 루트에서 `docker build -t opong-frontend ./frontend`로 이미지를 만들 수 있습니다. 이 명령은 현재 작업 환경에 Docker가 없어 실행 검증하지 못했습니다.

## 화면과 데이터 연결

| 화면 | 현재 동작 |
| --- | --- |
| 로그인 (`app.py`) | 데모 ID만 검사하고 브라우저 세션을 시작. 비밀번호 필드는 시안 표시용 비활성 상태 |
| 이벤트 생성 | 이름·대상·기간(1~7일)·목적을 검증하고 `POST /api/events/generate` 요청. 선택적으로 Test Mode의 별도 경로 사용. 고정 예시 입력과 별도 데모 체험 제공 |
| 진행 상태 / 결과 | 현재 Task의 기획안, 일자별 미션·보상, 규칙 검증 근거, AutoHealing, Critic 상태를 표시. 게임 규칙 검증과 AI 최종 검수를 나란히 배치하고 그 아래에 전체 너비 AutoHealing, 운영자 승인 순서로 표시. 승인·수정 요청·거절과 Task·Trace 새로고침 제공 |
| 운영 · 디버그 | 현재 Task의 구조화된 Trace, 주체별 필터, 세션 입력값, Backend Live·Ready 확인 제공 |

API 호출은 `frontend/services/backend_client.py`에 모았습니다. 현재 연결 대상은 생성 2개, Task 조회, 승인, Trace 조회, Health 2개로 총 7개 엔드포인트입니다. Backend가 아직 해당 계약을 구현하지 않은 경우 요청은 연결 또는 HTTP 오류로 표시됩니다. Frontend는 Backend 성공 결과를 임의로 생성하지 않습니다.

### Backend 없이 예시 화면 체험

이벤트 생성 화면에서 오른쪽 예시 하나를 선택한 다음 **선택한 예시로 체험하기**를 누르면 `frontend/services/demo_data.py`의 고정된 Task·Trace가 결과 화면에 나타납니다. 신규 유저 7일은 **시연용 MCP 규칙·아이템 조회 → 규칙 검증 실패 → AutoHealing → MCP 재조회 → 재검증 PASS**, 복귀 유저 5일은 **시연용 MCP 규칙·아이템 조회 → 정상 검증**, 주말 3일은 **MCP Timeout → 기술 재시도 3회 → Safe Stop** 시나리오입니다. 주말 3일 시나리오는 검증되지 않은 기획안을 결과나 승인 대상으로 표시하지 않습니다. 결과·운영 화면에는 **데모 데이터** 경고가 표시됩니다.

이 경로는 AI, MCP, Backend 및 실제 게임 규칙 검증을 실행하지 않습니다. 데모의 승인·수정 요청·거절, Task·Trace 새로고침, Backend Health 조회는 비활성화됩니다. 실제 API로 생성하려면 기존 **AI로 이벤트 생성하기** 버튼을 사용합니다.

결과 화면 상단의 장애 검증 요약은 Task와 Trace에 기록된 오류 유형, 실패 단계, 복구 조치, 재검증 및 최종 상태를 보여줍니다. AutoHealing 카드는 이벤트의 `error_type`·`reason`으로 실패 이유를, `recovery_action`·`target`과 수정 전후 값으로 대응 내용을 표시합니다. 최종 복구 이벤트의 `revalidation_status=PASS`와 Task의 최종 `rule_validation.is_valid=true`가 함께 있을 때만 `rule_validation.reason`·`evidence`를 성공 근거로 표시합니다. 근거가 없으면 상세 성공 이유를 제공받지 못했다고 명시하며 다른 이벤트의 최종 검증 근거를 가져다 붙이지 않습니다. 원본 비교·검증 JSON은 펼쳐볼 수 있습니다. 7일 데모의 예상 레벨 Lv.15와 수정 전후 필요 레벨 Lv.30→Lv.10은 화면 체험용 고정 근거이며 실제 게임 규칙 검증은 수행하지 않습니다. `workflow_status=FAILED`는 안전 종료 화면으로 분기해 오류와 재시도 횟수, Trace 이동을 제공하며 기획안과 승인 기능은 숨깁니다. Workflow 재생과 운영 Trace는 실패·재시도·Fallback·재검증을 구분해 표시합니다. Trace나 API에 없는 복구 시간과 자동 복구율은 계산하거나 표시하지 않습니다.

이벤트 생성은 완료까지 동기 응답을 기다리며 최대 180초를 설정했습니다. 연결 끊김 또는 시간 초과 후 같은 입력을 다시 제출하면 같은 `idempotency_key`를 재사용합니다. `workflow_status=FAILED` Task는 API 오류와 구분해 결과 화면에 실패 원인과 Trace 이동을 표시합니다. 승인 요청에는 현재 `result_version`을 보내고, `STALE_RESULT_VERSION`이면 Task를 다시 조회합니다.

결과 화면의 Workflow 연출은 조회된 Trace의 순서로 **완료된 기록을 재생**합니다. 지도 위쪽의 진행 단계 5개는 이벤트 기획 → MCP 데이터 조회 → 게임 규칙 검증 → AI 최종 검수 → 운영자 승인이고, 아래쪽의 담당 에이전트 4개는 Event Planner, Balance & Rule, Critic, Supervisor입니다. Supervisor의 복구 지시와 Event Planner의 기획안 재생성을 별도 에이전트 박스에서 보여주며, 규칙 재검증과 MCP 재조회는 해당 위쪽 단계의 방문 횟수로 표시합니다. 안전 종료는 실제 Trace의 Supervisor 상태와 상세 카드에 표시하고 승인 단계를 실행한 것처럼 표시하지 않습니다. 데모 Trace에는 각 검증 전의 시연용 MCP 조회 기록이 포함되지만 실제 서버를 호출하지 않습니다. 실제 API의 Trace에 `actor=mcp` 기록이 없으면 프론트엔드는 MCP 단계를 성공한 것으로 추정하지 않고 `Trace 기록 없음`으로 표시합니다. 연결선과 화살촉은 Streamlit 화면 삽입 후 JavaScript에서 생성하며 실제 Trace가 지나간 연결에만 표시합니다. 재시도처럼 같은 연결을 되돌아가면 가장 최근 이동 방향에 맞춰 화살촉을 바꿉니다. MCP Server는 Agent로 분류하지 않고 운영 담당자 승인 대기는 Task 상태를 근거로 표시합니다. 현재 재생 중인 단계와 에이전트, 이동 경로를 강조하고, 별도 카드에 Trace의 실행 주체·상태·사유·순서를 표시합니다. 동일 단계를 여러 번 거치면 방문 횟수를 보여주며, 이전·다음·일시정지·다시 보기로 기록을 살펴볼 수 있습니다. 연출 시간은 Agent 실행 시간이나 실시간 진행률을 의미하지 않습니다. Trace가 없으면 재생하지 않습니다. 동작 줄이기 설정에서는 애니메이션 없이 최종 상태를 표시합니다.

## 현행 계약의 화면 범위

선택 표시 필드인 `event_concept`, `operation_summary`, `day_title`, `planning_reason`은 응답에 값이 있을 때만 해당 내용을 표시합니다. 최근 Task 목록, 게임 데이터 건수, MCP 개별 연결 상태, 규칙 검사별 PASS 목록, 정확한 총 실행 시간, 원시 시스템 로그는 현재 API에서 제공하지 않으므로 표시하지 않습니다. `MCP 실행 기록`과 `시스템 실행 기록` 탭은 별도 로그 API가 아니라 Trace의 `actor`로 필터링한 결과입니다.

브라우저 세션에는 현재 Task 한 건과 입력값을 보관합니다. 새 브라우저 세션에서 과거 Task를 나열하거나 ID로 불러오는 UI는 아직 없습니다. 데모 ID 검사는 실제 인증이나 Backend 승인 권한 검증을 대신하지 않습니다.
