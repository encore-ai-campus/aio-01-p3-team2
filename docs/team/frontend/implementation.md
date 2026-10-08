# 프론트엔드 구현 현황

## 2026-10-08 로컬 통합 화면 검증

`backend-test`에 최신 `yejin` 프론트엔드 변경을 병합한 뒤 로컬 Backend와 Frontend를 함께 실행했다. Backend의 `/health/ready`와 `/docs`, Frontend의 `/_stcore/health`와 첫 화면이 모두 HTTP 200으로 응답했다. 사용자가 병합 후 화면 검증 통과를 확인했다. 이 확인은 실제 OpenAI·MCP·PostgreSQL 연동이나 AWS 배포 검증을 의미하지 않는다.

같은 병합 상태에서 `ruff check .`, `ruff format --check .`, Frontend Python 구문 검사가 통과했다. Backend Pytest는 41개 통과, 2개 건너뜀이다. 건너뛴 두 항목은 `POSTGRES_TEST_URL`이 필요한 PostgreSQL 통합 테스트다.

현재 `frontend/`는 Streamlit 기반 데모 로그인과 이벤트 생성, 결과·승인, 운영·디버그 화면을 제공합니다. 화면은 제공된 시안의 레이아웃을 참고해 Streamlit 컴포넌트와 HTML/CSS로 구성했습니다. Backend 구현은 이 작업에 포함하지 않았습니다.

로그인 화면과 사이드바는 `frontend/assets/event_quest_ai_logo.png`의 Event Quest AI 로고를 함께 사용합니다. 로그인 왼쪽 소개 문구는 이벤트 기획·규칙 검증·오류 수정 기능을 안내합니다. 로그인 카드 바깥 배경은 로고 색상에 맞춘 CSS 그라데이션입니다.

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
| 이벤트 생성 | 이름·대상·기간(1~7일)·목적을 검증하고 `POST /api/events/generate` 요청. 선택적으로 Test Mode의 별도 경로 사용. 고정 예시 입력과 별도 데모 체험 제공. 저장된 이벤트 목록 API가 제공되면 아래 목록에서 과거 Task 선택 가능 |
| 진행 상태 / 결과 | 현재 Task의 기획안, 일자별 미션·보상과 기획안 요약, 규칙 검증 근거, AutoHealing, Critic 상태를 표시. 게임 규칙 검증과 AI 최종 검수를 나란히 배치하고 그 아래에 전체 너비 AutoHealing, 운영자 승인 순서로 표시. 승인·수정 요청·거절과 Task·Trace 새로고침 제공 |
| 운영 · 디버그 | 현재 Task의 실행 요약과 단일 상세 Trace, 전체·MCP·시스템 실행 주체 필터, Backend Live·Ready 확인 제공 |

API 호출은 `frontend/services/backend_client.py`에 모았습니다. 현행 MVP 계약의 연결 대상은 생성 2개, Task 조회, 승인, Trace 조회, Health 2개로 총 7개 엔드포인트입니다. 여기에 미구현 확장 후보인 Task 목록 조회 `GET /api/tasks` 클라이언트를 추가했습니다. Backend가 해당 API를 구현하지 않은 경우 오류·미구현 안내와 함께 로컬 데모 목록을 표시합니다. Frontend는 Backend 성공 결과나 DB 저장 기록을 임의로 생성하지 않습니다.

### 저장된 이벤트 다시 보기: Backend 목록 API 필요

이벤트 생성 화면의 **저장된 이벤트 기획안** 영역은 최초 진입과 새로고침 시 `GET /api/tasks?limit=10`을 호출하고, 추가 페이지가 있으면 `GET /api/tasks?limit=10&cursor=...`를 호출합니다. 이 목록 API는 **아직 Backend에 구현되지 않았습니다.** 프론트엔드가 기대하는 응답은 `{"items":[{"task_id":"...","event_name":"...","target_user":"new_user","duration":7,"workflow_status":"WAITING_APPROVAL","created_at":"...","result_version":1}],"next_cursor":null}` 형식입니다. 최신순 정렬, DB 조회, 요청자의 접근 권한 판단은 Backend가 맡아야 합니다. 데모 ID는 인증 수단이 아니므로 프론트엔드의 `actor_id`만 믿고 권한을 결정해서는 안 됩니다.

목록 API가 연결되지 않거나 DB 목록이 비어 있으면 같은 영역에 고정된 예시 3건을 **데모 데이터 · DB에 저장되지 않음**으로 표시합니다. 각 **미리보기** 버튼은 로컬 Task·Trace를 여는 시연 기능이며 실제 저장 이력이 아닙니다. 실제 목록을 받으면 데모 미리보기 대신 DB 목록만 표시합니다.

목록에서 **기획안 보기**를 선택하면 기존 `GET /api/tasks/{task_id}`와 `GET /api/tasks/{task_id}/trace`로 해당 Task를 다시 읽고 결과 화면을 엽니다. Trace 조회만 실패하면 Task 결과 화면은 열고 Trace 재조회는 결과 화면에서 다시 시도합니다. `FAILED` 항목은 **기록 보기**로 표시하고 안전 종료 화면을 엽니다. 목록은 DB에 저장된 실제 Task만 대상으로 하며 고정 데모 체험 결과를 섞지 않습니다. Backend 목록 API가 없거나 연결되지 않으면 빈 DB 기록을 꾸며서 표시하지 않습니다.

### 결과 화면의 기획안 요약

일자별 이벤트 구성 아래에 현재 결과의 시작·중간·마지막 일차에서 발췌한 **참여 흐름**과 기재된 보상 항목을 모은 **보상 설계 요약**을 표시합니다. **운영·검토 메모**는 Backend의 `operation_review_notes`가 있을 때만 미션 운영 방식·보상 설계 이유·Critic 검토 의견을 표시합니다. 필드가 없는 이전 Task에서는 메모가 제공되지 않았음을 알리며, `source=mock`이면 실제 AI 의견이 아닌 Mock 생성임을 표시합니다. 참여율·매출 같은 성과 수치는 생성하지 않습니다.

### Backend 없이 예시 화면 체험

이벤트 생성 화면에서 오른쪽 예시 하나를 선택한 다음 **선택한 예시로 체험하기**를 누르면 `frontend/services/demo_data.py`의 고정된 Task·Trace가 결과 화면에 나타납니다. 복귀 유저 5일은 **기획용 MCP 조회 → Supervisor의 Planner 선택 → Planner 초안 → Supervisor의 Balance & Rule 선택 → 검증용 MCP 조회 → Balance & Rule 검증 → Supervisor의 Critic 선택 → Critic 검토 → Supervisor의 승인 대기 결정**을 보여주는 정상 경로입니다. 신규 유저 7일은 기획용 데이터를 받은 뒤에도 잘못된 레이드 미션을 포함시키는 **의도적 오류 주입 데모**로, 규칙 검증 실패 → Supervisor 수정 결정 → Planner 수정 → MCP 재조회 → 재검증 PASS를 보여줍니다. 주말 3일은 **초안 생성 전 기획용 MCP 조회 실패 → 기술 재시도 3회 → Safe Stop** 시나리오이며, Planner·검증·승인 단계를 실행한 것처럼 표시하지 않습니다. 결과·운영 화면에는 **데모 데이터** 경고가 표시됩니다.

이 경로는 AI, MCP, Backend 및 실제 게임 규칙 검증을 실행하지 않습니다. 데모의 승인·수정 요청·거절, Task·Trace 새로고침, Backend Health 조회는 비활성화됩니다. 실제 API로 생성하려면 기존 **AI로 이벤트 생성하기** 버튼을 사용합니다.

결과 화면 상단의 장애 검증 요약은 Task와 Trace에 기록된 오류 유형, 실패 단계, 복구 조치, 재검증 및 최종 상태를 보여줍니다. AutoHealing 카드는 이벤트의 `error_type`·`reason`으로 실패 이유를, `recovery_action`·`target`과 수정 전후 값으로 대응 내용을 표시합니다. 최종 복구 이벤트의 `revalidation_status=PASS`와 Task의 최종 `rule_validation.is_valid=true`가 함께 있을 때만 `rule_validation.reason`·`evidence`를 성공 근거로 표시합니다. 근거가 없으면 상세 성공 이유를 제공받지 못했다고 명시하며 다른 이벤트의 최종 검증 근거를 가져다 붙이지 않습니다. 원본 비교·검증 JSON은 펼쳐볼 수 있습니다. 7일 데모의 예상 레벨 Lv.15와 수정 전후 필요 레벨 Lv.30→Lv.10은 화면 체험용 고정 근거이며 실제 게임 규칙 검증은 수행하지 않습니다. `workflow_status=FAILED`는 안전 종료 화면으로 분기해 오류와 재시도 횟수, Trace 이동을 제공하며 기획안과 승인 기능은 숨깁니다. Workflow 재생과 운영 Trace는 실패·재시도·Fallback·재검증을 구분해 표시합니다. Trace나 API에 없는 복구 시간과 자동 복구율은 계산하거나 표시하지 않습니다.

Clash 공개 표본 경로에서 복구 이벤트가 반환되면 Workflow 지도 위에 **오류 감지 → Supervisor 결정 → 복구 대상 → 재검증 결과**를 복구 횟수별로 표시합니다. AutoHealing 카드에는 Day별 미션 목표와 보상 수량의 수정 전후를 비교합니다. Clash 검증은 MCP를 다시 호출하지 않고 최초에 받은 동일 표본을 사용하므로 지도에서 검증 데이터 칸을 `Clash 표본 재사용`으로 표시합니다. 실패 Trace만 있고 복구 이벤트가 없는 과거 Task는 복구 여부를 추측하지 않고 기록 누락을 알립니다. 실패 Trace도 복구 이벤트도 없는 정상 실행은 복구가 필요하지 않았다고 표시합니다. 이 화면은 의도적 장애 주입 없이 실제 Backend 응답만 재생하며, 기존 Task의 누락된 기록을 소급 생성하지 않습니다.

이벤트 생성은 완료까지 동기 응답을 기다리며 최대 180초를 설정했습니다. 연결 끊김 또는 시간 초과 후 같은 입력을 다시 제출하면 같은 `idempotency_key`를 재사용합니다. `workflow_status=FAILED` Task는 API 오류와 구분해 결과 화면에 실패 원인과 Trace 이동을 표시합니다. 승인 요청에는 현재 `result_version`을 보내고, `STALE_RESULT_VERSION`이면 Task를 다시 조회합니다.

결과 화면의 Workflow 연출은 조회된 Trace의 순서로 **완료된 기록을 재생**합니다. 지도 위쪽 네 박스는 기획용 MCP 조회 → 이벤트 기획 → 검증용 MCP 조회 → 운영자 승인이고, 아래쪽의 담당 에이전트 네 박스는 Event Planner, Balance & Rule, Critic, Supervisor입니다. MCP 검증 데이터 조회 뒤에는 Balance & Rule Agent의 게임 규칙 검증 Trace와 Critic Agent의 품질 검토 Trace를 거쳐 운영자 승인으로 연결합니다. `actor=mcp` 기록의 `phase=planning_context` 또는 `action=get_event_planning_context`는 기획용 조회에, 나머지 MCP 기록은 검증용 조회에 대응합니다. `phase=revalidation`은 수정안의 재검증용 조회로 표시합니다. 백엔드가 `phase`를 제공하지 않는 기존 MCP 기록은 검증용으로 표시하며, 기획용 조회를 추정해 성공 처리하지 않습니다. Supervisor의 정상 단계 전환과 오류 복구 지시를 구분하고 Event Planner의 기획안 재생성을 별도 박스에서 보여주고, 동일 단계 재방문은 방문 횟수로 표시합니다. 안전 종료는 실제 Trace의 Supervisor 상태와 상세 카드에 표시하며 실행되지 않은 Planner·승인 단계를 성공한 것처럼 표시하지 않습니다. 연결선과 화살촉은 Streamlit 화면 삽입 후 JavaScript에서 생성하며 실제 Trace가 지나간 연결에만 표시합니다. MCP Server는 Agent로 분류하지 않고 운영 담당자 승인 대기는 Task 상태를 근거로 표시합니다. 이전·다음·일시정지·다시 보기로 기록을 살펴볼 수 있으며 연출 시간은 실제 Agent 실행 시간이나 실시간 진행률을 의미하지 않습니다. Trace가 없으면 재생하지 않고 동작 줄이기 설정에서는 애니메이션 없이 최종 상태를 표시합니다.

지도 아래쪽 네 에이전트 박스는 공통의 「담당 에이전트」 문구 대신 각각 **이벤트 기획·수정**, **게임 데이터 비교·검증**, **이벤트 기획안 종합 검토**, **다음 단계 선택·오류 복구** 역할을 표시합니다. 에이전트 이름과 Trace 기반 상태 표시는 유지합니다.

기획 전 MCP 조회와 `phase` 구분은 새 Backend Trace 계약이 제공될 때 실제 결과로 확인할 수 있습니다. 현재 고정 데모는 새 순서를 시각적으로 예시할 뿐 실제 MCP·Agent 실행 기록이 아닙니다. Backend가 기존 Trace를 반환하면 프론트엔드는 없는 기획용 조회나 Supervisor 결정을 임의로 추가하지 않습니다. 정상 경로 결정은 Backend Trace의 `actor=supervisor`, `phase=routing`, `action=RUN_AGENT` 또는 `WAIT_APPROVAL`, `next_agent`, `reason`, `status`가 실제 제공될 때만 표시합니다. 데모 Trace의 같은 필드는 시연용 고정 기록입니다.

## 현행 계약의 화면 범위

선택 표시 필드인 `event_concept`, `operation_summary`, `day_title`, `planning_reason`은 응답에 값이 있을 때만 해당 내용을 표시합니다. 최근 Task 목록은 위 미구현 API가 연결되었을 때만 표시합니다. 게임 데이터 건수, MCP 개별 연결 상태, 규칙 검사별 PASS 목록, 정확한 총 실행 시간, 원시 시스템 로그는 현재 API에서 제공하지 않으므로 표시하지 않습니다. 운영·디버그 화면은 Critic·승인·재시도·자동 수정 상태를 위쪽 실행 요약에 모으고, 상세 Trace 하나에서 `전체 / MCP / 시스템` 필터로 `actor` 기록을 골라 봅니다. `시스템`은 `backend`, `database`, `supervisor` 기록을 뜻합니다. 별도의 입력 데이터 탭은 제공하지 않습니다.

브라우저 세션에는 현재 Task 한 건과 입력값, 이번 세션에서 조회한 목록 페이지를 보관합니다. 새 브라우저 세션에서도 목록 API가 제공되면 DB에서 목록을 다시 읽어 과거 Task를 선택할 수 있습니다. 데모 ID 검사는 실제 인증이나 Backend 승인 권한 검증을 대신하지 않습니다.
