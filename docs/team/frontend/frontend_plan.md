# 프론트엔드 기획안

> 노션 원문: https://app.notion.com/p/3f281f16897680cc9736d11fb27496b2
> 노션 최종 수정: 2026-10-07T10:20:44.488Z

## 1. 문서 목적
본 문서는 **게임 운영팀을 위한 AI 이벤트 기획 도우미**의 Frontend 구현 범위와 화면 구성을 정의합니다.
Frontend는 **Streamlit**을 사용하여 구현하며, Okepong Games의 운영 담당자가 Opong Heroes의 이벤트를 생성하고 AI Agent의 검수 및 AutoHealing 과정을 확인한 뒤 최종 결과를 승인할 수 있는 내부 운영 화면을 제공합니다.
Frontend의 핵심 역할은 다음과 같습니다.
```plain text
이벤트 조건 입력
↓
AI 이벤트 생성 요청
↓
Agent Workflow 결과 확인
↓
오류 및 AutoHealing 결과 확인
↓
생성된 이벤트 기획안 확인
↓
운영 담당자 최종 승인 / 수정 요청 / 거절
↓
Trace 및 시스템 상태 확인
```
Frontend에서는 이벤트 기획이나 게임 규칙 검증을 직접 수행하지 않습니다.
실제 Workflow 실행, Agent 호출, Validation, AutoHealing, Retry 등의 처리는 Backend에서 담당하며 Frontend는 **Backend API를 호출하고 결과를 사용자가 이해하기 쉬운 화면으로 제공하는 역할**을 담당합니다.
---
# 2. 주요 사용자
### 주요 사용자
**Okepong Games 운영팀**
주요 사용자는 게임 이벤트를 기획하거나 운영하는 담당자입니다.
### Demo 사용자
```plain text
이름: 김하늘
소속: 운영팀
역할: 이벤트 기획자
Demo ID: liveops01
```
실제 사내 인증 시스템은 구현하지 않으며 프로젝트 시연을 위한 Demo Login만 제공합니다.
로그인 상태는 Streamlit의 `session_state`를 사용하여 관리합니다.
---
# 3. Frontend 기술 구성
```plain text
Frontend Framework
→ Streamlit

Backend 통신
→ FastAPI REST API

상태 관리
→ st.session_state

배포
→ Docker

Frontend 실행 환경
→ AWS EC2
```
Frontend에서는 Backend URL을 환경변수로 관리합니다.
```plain text
frontend/.env
frontend/.env.docker
```
Frontend에서 직접 OpenAI API, PostgreSQL, MCP Server에 접근하지 않습니다.
모든 요청은 Backend API를 통해 처리합니다.
```plain text
Streamlit
   ↓
FastAPI Backend
   ↓
Supervisor / Agent / MCP / DB
```
## 3-1. Streamlit 구현 방식
이번 MVP는 Streamlit 기본 컴포넌트를 중심으로 구현하고, 필요한 경우 최소한의 CSS를 추가하여 운영 Dashboard 형태로 구성합니다.
<table header-row="true">
<tr>
<td>화면 요소</td>
<td>Streamlit 구현</td>
</tr>
<tr>
<td>왼쪽 메뉴</td>
<td>`st.sidebar`</td>
</tr>
<tr>
<td>페이지 이동</td>
<td>`st.page_link` 또는 Streamlit multipage</td>
</tr>
<tr>
<td>이벤트명</td>
<td>`st.text_input()`</td>
</tr>
<tr>
<td>이벤트 대상</td>
<td>`st.selectbox()`</td>
</tr>
<tr>
<td>이벤트 기간</td>
<td>`st.selectbox()`</td>
</tr>
<tr>
<td>이벤트 목적</td>
<td>`st.text_area()`</td>
</tr>
<tr>
<td>Normal / Test Mode</td>
<td>`st.radio()` 또는 `st.segmented_control()`</td>
</tr>
<tr>
<td>Fault Type</td>
<td>`st.selectbox()`</td>
</tr>
<tr>
<td>카드 영역</td>
<td>`st.container(border=True)`</td>
</tr>
<tr>
<td>2열 화면</td>
<td>`st.columns()`</td>
</tr>
<tr>
<td>상태 요약</td>
<td>`st.metric()` / `st.markdown()`</td>
</tr>
<tr>
<td>생성 버튼</td>
<td>`st.button()`</td>
</tr>
<tr>
<td>Loading</td>
<td>`st.spinner()`</td>
</tr>
<tr>
<td>긴 결과 화면</td>
<td>여러 `st.container()`를 세로로 반복 배치</td>
</tr>
<tr>
<td>Trace</td>
<td>`st.dataframe()` 또는 반복 Row + Expander</td>
</tr>
</table>
## 3-2. 화면 Layout 기준
전체 페이지는 Wide Layout을 사용합니다.
```python
st.set_page_config(page_title="OPONG HEROES LIVE OPS", layout="wide")
```
Page 1 이벤트 생성 화면은 메인 입력 영역과 보조 정보 영역을 약 70:30 비율로 구성합니다.
```python
main_col, guide_col = st.columns([7, 3])
```
```plain text
┌─────────────────────────────────────────────────────┐
│ 이벤트 생성                                         │
│                                                     │
│ ┌────────────────────────────┐ ┌─────────────────┐ │
│ │ 이벤트 기본 정보            │ │ 참고 가이드      │ │
│ │ 이벤트명 / 대상             │ │ 게임 기준 안내   │ │
│ │ 기간 / 목적                 │ │ Test Mode 안내   │ │
│ │ Normal / Test              │ │ 고정 참고 정보   │ │
│ │ Fault Type(Test only)      │ │                 │ │
│ │                            │ │                 │ │
│ │ [ AI 이벤트 생성하기 ]      │ │                 │ │
│ └────────────────────────────┘ └─────────────────┘ │
└─────────────────────────────────────────────────────┘
```
Page 2는 결과를 표 한 장으로 압축하지 않고, 이벤트 기본 정보와 Day별 카드를 위에서 아래로 이어 붙이는 **세로 스크롤형 결과 보고서**로 구현합니다.
## 3-3. 디자인 원칙
- 전체 UI: 흰색 + 연한 블루 기반의 내부 운영 Dashboard
- Sidebar: `OPONG HEROES / LIVE OPS` 고정
- Card: 둥근 모서리와 연한 Border 사용
- `PASS / SUCCESS`: 초록 계열
- `WAITING / PENDING`: 노랑 계열
- `ERROR / FAILED`: 빨강 계열
- `INFO / RUNNING`: 파랑 계열
- 이벤트 결과가 가장 큰 시각적 비중을 차지하도록 구성
- CSS는 카드 Radius, 간격, Badge, 버튼 정도의 최소 범위로 사용
- Streamlit 기본 기능으로 가능한 부분은 과도한 Custom HTML 없이 구현
---
# 4. 전체 화면 구성
로그인 이후 Frontend는 총 **3개의 주요 페이지**로 구성합니다.
```plain text
Demo Login
    ↓

① 이벤트 생성
    ↓
② 진행 상태 / 결과
    ↓
③ 운영 · 디버그
```
### Page 1
**이벤트 생성**
운영 담당자가 생성할 이벤트 조건을 입력하고 AI Workflow를 시작합니다.
### Page 2
**진행 상태 / 결과**
Agent Workflow, 오류 발생 및 AutoHealing 과정, 최종 이벤트 결과를 확인하고 Human Approval을 진행합니다.
### Page 3
**운영 · 디버그**
현재 Task의 상세 Trace와 Backend Health를 확인합니다. Agent / Supervisor / MCP Tool의 Retry·Fallback 이력도 별도 Tool Log API가 아니라 Trace에서 확인합니다.
---
# 5. 공통 화면 구성
로그인 이후 모든 페이지는 동일한 Sidebar를 사용합니다.
```plain text
Okepong Games

Opong Heroes
LIVE OPS

----------------

□ 이벤트 생성

□ 진행 상태 / 결과

□ 운영 · 디버그
```
현재 선택된 메뉴는 활성화 상태로 표시합니다.
Sidebar 상단에는 Okepong Games와 Opong Heroes의 서비스 정보를 표시합니다.
---
# 6. Demo Login
## 6-1. 목적
프로젝트 시연을 위한 간단한 직원 로그인 화면입니다.
실제 사내 로그인이나 인증 시스템은 구현하지 않습니다.
## 6-2. 화면 구성
```plain text
OPONG HEROES
LIVE OPS CENTER

직원 ID

[ liveops01             ]

[ 로그인 ]
```
## 6-3. 동작
직원 ID 입력 후 로그인 버튼을 선택합니다.
로그인 성공 시 다음 정보를 Streamlit Session State에 저장합니다.
```plain text
logged_in = true
actor_id = liveops01
```
로그인하지 않은 경우 이벤트 생성 및 운영 화면에 접근하지 못하도록 처리합니다.
---
# 7. Page 1. 이벤트 생성
## 7-1. 화면 목적
운영 담당자가 새로운 이벤트를 생성하기 위한 기본 조건을 입력하고 AI Multi-Agent Workflow 실행을 요청하는 화면입니다.
---
## 7-2. 실행 모드 선택
화면 상단에서 Workflow 실행 방식을 선택합니다.
### Normal Mode
정상적인 이벤트 생성 및 검수 Workflow를 실행합니다.
```plain text
Normal Mode
→ 정상적인 이벤트 생성
→ Agent 검수
→ Critic 검수
→ Human Approval
```
사용 API
```plain text
POST /api/events/generate
```
### Test Mode
AutoHealing 기능을 시연하거나 테스트할 때 사용하는 모드입니다.
```plain text
Test Mode
→ 의도적으로 장애 발생
→ 오류 감지
→ Retry / Fallback / AutoHealing
→ 복구 결과 확인
```
사용 API
```plain text
POST /api/test/events/generate
```
Test Mode의 **화면 노출 여부와 실제 장애 주입 권한은 분리**합니다.
Frontend에서는 다음 환경변수로 Test Mode UI 노출 여부를 결정합니다.
```plain text
SHOW_TEST_MODE=true
```
Backend에서는 다음 환경변수로 실제 Fault Injection 실행 가능 여부를 최종 판단합니다.
```plain text
ENABLE_FAULT_INJECTION=true
```
즉, Frontend에서 Test Mode가 보여도 Backend의 `ENABLE_FAULT_INJECTION=false`이면 장애 주입은 실행되지 않습니다.
---
# 8. Test Mode 장애 주입
Test Mode를 선택한 경우에만 **장애 주입 유형**을 표시합니다.
예시:
```plain text
장애 주입 유형

[ mission_unreachable_once ▼ ]
```
선택 가능한 장애 예시는 다음과 같습니다.
<table header-row="true">
<tr>
<td>장애 유형</td>
<td>설명</td>
</tr>
<tr>
<td>mission_unreachable_once</td>
<td>수행할 수 없는 미션을 생성</td>
</tr>
<tr>
<td>agent_failure_once</td>
<td>Agent가 최초 1회 실패</td>
</tr>
<tr>
<td>primary_tool_error_once</td>
<td>Primary Tool 실행 오류</td>
</tr>
<tr>
<td>mcp_timeout_once</td>
<td>MCP Tool Timeout</td>
</tr>
<tr>
<td>db_connection_error_once</td>
<td>DB 최초 연결 실패</td>
</tr>
<tr>
<td>validation_error_once</td>
<td>Agent Output Validation 오류</td>
</tr>
<tr>
<td>retry_limit_exceeded</td>
<td>Retry 한도를 초과하도록 장애 지속</td>
</tr>
</table>
Normal Mode에서는 장애 주입 관련 UI를 표시하지 않습니다.
---
# 9. 이벤트 기본 정보 입력
사용자가 입력하는 이벤트 정보는 MVP Event Brief로 사용합니다.
### 입력 항목
<table header-row="true">
<tr>
<td>항목</td>
<td>설명</td>
</tr>
<tr>
<td>이벤트명</td>
<td>생성할 이벤트 이름</td>
</tr>
<tr>
<td>이벤트 대상</td>
<td>신규 / 복귀 / 전체 유저</td>
</tr>
<tr>
<td>이벤트 기간</td>
<td>1\~7일</td>
</tr>
<tr>
<td>이벤트 목적</td>
<td>이벤트를 진행하는 목적</td>
</tr>
</table>
### 입력 예시
```plain text
이벤트명
돌아온 영웅 5일 성장 이벤트

이벤트 대상
기존·복귀 유저

이벤트 기간
5일

이벤트 목적
복귀 유저의 핵심 콘텐츠 재적응을 돕고,
게임 재접속률을 높이기 위한 성장 지원 이벤트
```
---
# 10. 이벤트 생성
입력을 완료하면 다음 버튼을 제공합니다.
```plain text
✨ AI 이벤트 생성하기
```
버튼을 선택하면 Frontend에서 Backend API를 호출합니다.
```plain text
Streamlit
↓
EventCreateRequest
↓
FastAPI
↓
Supervisor Orchestrator
↓
Multi-Agent Workflow
```
API 요청 중에는 Streamlit Spinner 등을 사용하여 처리 중임을 표시합니다.
예:
```plain text
AI Agent가 이벤트를 기획하고 검수하고 있습니다...
```
현재 MVP는 동기식 API 방식이므로 Agent 실행 과정을 실시간 Streaming하지 않습니다.
Backend Workflow가 완료된 후 반환된 Workflow State와 Trace를 기반으로 실행 과정을 화면에 표시합니다.
---
# 11. 게임 기본 데이터 안내
이벤트 생성 화면 하단에는 AI가 이벤트를 생성하고 검증할 때 참고하는 게임 데이터 일부를 표시합니다.
예시:
<table header-row="true">
<tr>
<td>콘텐츠명</td>
<td>필요 레벨</td>
<td>설명</td>
</tr>
<tr>
<td>일반 사냥 (`HUNT_001`)</td>
<td>Lv 1</td>
<td>필드 몬스터 사냥</td>
</tr>
<tr>
<td>일반 던전 (`DUNGEON_001`)</td>
<td>Lv 10</td>
<td>일반 던전 콘텐츠</td>
</tr>
<tr>
<td>레이드 (`RAID_001`)</td>
<td>Lv 30</td>
<td>8인 레이드 콘텐츠</td>
</tr>
</table>
목적은 사용자가 AI가 임의로 판단하는 것이 아니라 **게임 데이터에 정의된 기준을 이용하여 이벤트를 검증한다는 점을 이해할 수 있도록 하는 것**입니다.
이번 MVP에서는 `GET /api/game-data`를 사용하지 않으므로 이 영역은 **시연용 고정 참고 정보**로 표시합니다. 실제 검증에 사용된 근거와 버전은 결과 화면의 `rule_validation.evidence`, `game_data_version`, `data_source`로 확인합니다.
---
# 12. Page 2. 진행 상태 / 결과
## 12-1. 화면 목적
AI Agent가 이벤트를 생성하고 검수한 결과를 확인하는 핵심 화면입니다.
다음 정보를 한 페이지에서 확인할 수 있도록 구성합니다.
```plain text
Workflow 진행 상태
+
Workflow 실행 기록
+
오류 / AutoHealing 과정
+
게임 규칙 검증 결과
+
생성된 이벤트 기획안
+
Human Approval
```
---
# 13. Workflow 진행 상태
Workflow의 주요 단계를 화면 상단에 표시합니다.
```plain text
이벤트 기획
Event Planner

        ↓

규칙 검증
Balance & Rule

        ↓

자동 복구
AutoHealing

        ↓

최종 검수
Critic

        ↓

승인
Human Approval
```
각 단계의 상태를 구분하여 표시합니다.
예:
```plain text
✓ 완료
● 진행
! 오류
○ 대기
```
예시:
```plain text
Event Planner       완료

Balance & Rule      오류 발생

AutoHealing         완료

Critic              완료

Human Approval      승인 대기
```
---
# 14. Workflow 실행 기록
Agent와 Supervisor의 실행 과정을 시간순으로 표시합니다.
실시간 Streaming Log가 아니라 Backend에서 반환된 Trace를 이용하여 표시합니다.
**중요:** 현재 MVP는 동기식 API 구조이므로 Workflow 실행 중 각 Agent 상태를 실시간으로 받지 않습니다. API 요청 중에는 Spinner/Loading 상태만 표시하고, Backend 응답이 완료된 뒤 Trace를 기반으로 Event Planner → Balance & Rule → AutoHealing → Critic → Human Approval의 실제 실행 이력을 재구성하여 보여줍니다.
### 예시
<table header-row="true">
<tr>
<td>시간</td>
<td>실행 주체</td>
<td>내용</td>
<td>상태</td>
</tr>
<tr>
<td>14:23:10</td>
<td>Event Planner</td>
<td>이벤트 초안 생성</td>
<td>완료</td>
</tr>
<tr>
<td>14:23:18</td>
<td>Event Planner</td>
<td>초안 생성 완료</td>
<td>완료</td>
</tr>
<tr>
<td>14:23:19</td>
<td>Balance & Rule</td>
<td>게임 규칙 검증</td>
<td>진행</td>
</tr>
<tr>
<td>14:23:20</td>
<td>Balance & Rule</td>
<td>mission_unreachable 감지</td>
<td>오류</td>
</tr>
<tr>
<td>14:23:21</td>
<td>Supervisor</td>
<td>AutoHealing 경로 결정</td>
<td>진행</td>
</tr>
<tr>
<td>14:23:25</td>
<td>Event Planner</td>
<td>이벤트 수정</td>
<td>완료</td>
</tr>
<tr>
<td>14:23:29</td>
<td>Balance & Rule</td>
<td>수정안 재검증</td>
<td>완료</td>
</tr>
<tr>
<td>14:23:32</td>
<td>Critic</td>
<td>최종 검수</td>
<td>완료</td>
</tr>
<tr>
<td>14:23:35</td>
<td>Human Approval</td>
<td>승인 대기</td>
<td>대기</td>
</tr>
</table>
---
# 15. AutoHealing 결과
AutoHealing이 발생한 경우 오류와 복구 과정을 별도 영역으로 표시합니다.
화면은 다음 3단계 구조로 구성합니다.
```plain text
오류 발생
↓
AutoHealing
↓
복구 결과
```
---
## 15-1. 오류 발생
예:
```plain text
오류 발생

Error Type
mission_unreachable

Day 7
레이드 보스 1회 처치

신규 유저 예상 레벨
Lv.15

레이드 필요 레벨
Lv.30
```
---
## 15-2. AutoHealing 과정
```plain text
AutoHealing

✓ 오류 원인 분석

✓ 수정 방향 결정

✓ Event Planner 재실행

✓ 수정된 이벤트 생성

✓ 전체 재검증
```
---
## 15-3. 복구 결과
예:
```plain text
복구 후 결과

수정 전
Day 7
레이드 보스 1회 처치
필요 레벨 Lv.30

↓

수정 후
Day 7
일반 던전 3회 완료
필요 레벨 Lv.10

✓ 검증 통과
```
한 Workflow에서 AutoHealing이 여러 번 발생할 수 있으므로 Backend의 `autohealing_events[]`를 `sequence` 순서대로 반복 표시합니다.
예:
```plain text
AutoHealing #1
mission_unreachable → Event Planner 재실행 → PASS

AutoHealing #2
mcp_tool_error → Fallback Tool 사용 → PASS
```
AutoHealing이 발생하지 않은 정상 Workflow의 경우 `autohealing_events=[]`이며 다음과 같이 표시합니다.
```plain text
AutoHealing 없음

모든 Validation을 정상 통과했습니다.
```
---
# 16. 생성된 이벤트 기획안
## 16-0. Frontend 화면 예시
프론트 화면은 아래 구조를 기준으로 구현합니다.
```plain text
┌──────────────────────────────────────────────────────────────┐
│  OPONG HEROES LIVE OPS                                      │
│                                                              │
│  신규 유저 7일 성장 이벤트                    Result v1      │
│  ● 승인 대기                           Critic PASS            │
├──────────────────────────────────────────────────────────────┤


                    AI 생성 이벤트 기획안


┌──────────────────────────────────────────────────────────────┐
│ 이벤트 기본 정보                                             │
│                                                              │
│ 이벤트 대상     신규 유저                                    │
│ 이벤트 기간     7일                                          │
│                                                              │
│ 이벤트 목적                                                  │
│ 신규 유저가 초반 핵심 콘텐츠를 단계적으로 경험하고           │
│ 게임에 자연스럽게 적응할 수 있도록 성장 동선을 제공합니다.    │
│                                                              │
│ 이벤트 컨셉                                                  │
│ "7일 동안 영웅 성장하기"                                     │
│                                                              │
│ 운영 방식                                                    │
│ 매일 지정된 성장 미션을 완료하면 보상을 획득합니다.           │
│ 일차가 증가할수록 던전 등 상위 콘텐츠를 경험하도록 구성합니다.│
└──────────────────────────────────────────────────────────────┘


                      이벤트 구성


┌──────────────────────────────────────────────────────────────┐
│ DAY 1  |  모험의 시작                                        │
│                                                              │
│ 미션        일반 사냥 20회                                   │
│ 콘텐츠      일반 사냥 (HUNT_001)                             │
│ 보상        회복 포션 ×10                                    │
│                                                              │
│ 기획 의도                                                    │
│ 게임의 기본 전투 방식에 익숙해지도록 구성                    │
└──────────────────────────────────────────────────────────────┘

                              ↓

┌──────────────────────────────────────────────────────────────┐
│ DAY 2  |  사냥 적응                                          │
│                                                              │
│ 미션        일반 사냥 30회                                   │
│ 콘텐츠      일반 사냥 (HUNT_001)                             │
│ 보상        성장 상자 ×1                                     │
│                                                              │
│ 기획 의도                                                    │
│ 초기 성장에 필요한 반복 플레이 유도                          │
└──────────────────────────────────────────────────────────────┘

                              ↓

┌──────────────────────────────────────────────────────────────┐
│ DAY 3  |  첫 번째 도전                                       │
│                                                              │
│ 미션        일반 던전 1회 클리어                              │
│ 콘텐츠      일반 던전 (DUNGEON_001)                          │
│ 보상        회복 포션 ×10                                    │
│                                                              │
│ 기획 의도                                                    │
│ 일반 사냥에서 던전 콘텐츠로 자연스럽게 이동                  │
└──────────────────────────────────────────────────────────────┘

                              ↓

┌──────────────────────────────────────────────────────────────┐
│ DAY 4  |  던전 성장                                          │
│                                                              │
│ 미션        일반 던전 2회 클리어                              │
│ 콘텐츠      일반 던전 (DUNGEON_001)                          │
│ 보상        성장 상자 ×1                                     │
│                                                              │
│ 기획 의도                                                    │
│ 던전 반복 플레이를 통해 성장 루프 경험                       │
└──────────────────────────────────────────────────────────────┘

                              ↓

┌──────────────────────────────────────────────────────────────┐
│ DAY 5  |  성장 가속                                          │
│                                                              │
│ 미션        일반 던전 3회 클리어                              │
│ 콘텐츠      일반 던전 (DUNGEON_001)                          │
│ 보상        한정 코스튬 ×1                                   │
│                                                              │
│ 기획 의도                                                    │
│ 중간 목표 달성에 대한 보상 제공                              │
└──────────────────────────────────────────────────────────────┘

                              ↓

┌──────────────────────────────────────────────────────────────┐
│ DAY 6  |  성장 마무리                                        │
│                                                              │
│ 미션        일반 던전 3회 클리어                              │
│ 콘텐츠      일반 던전 (DUNGEON_001)                          │
│ 보상        성장 상자 ×1                                     │
│                                                              │
│ 기획 의도                                                    │
│ 최종 미션 진입 전 성장 상태 유지                             │
└──────────────────────────────────────────────────────────────┘

                              ↓

┌──────────────────────────────────────────────────────────────┐
│ DAY 7  |  7일 성장 완료                                      │
│                                                              │
│ 미션        일반 던전 3회 클리어                              │
│ 콘텐츠      일반 던전 (DUNGEON_001)                          │
│ 보상        한정 코스튬 ×1                                   │
│                                                              │
│ 기획 의도                                                    │
│ 7일 이벤트 완료에 대한 최종 보상 제공                        │
└──────────────────────────────────────────────────────────────┘
```
그리고 **그 아래부터 검증 결과가 이어지는 구조**로 구현합니다.
```plain text
──────────────────────────────────────────────────────────────

게임 규칙 검증

✓ PASS

검증 결과
모든 게임 규칙 검증을 통과했습니다.

검증 근거
expected_level : 15
required_level : 10

Game Data Version
tinyheroes-1.0.0

Data Source
primary

──────────────────────────────────────────────────────────────

AutoHealing

✓ AutoHealing 없음
최초 검증에서 모든 게임 규칙을 통과했습니다.

Retry 0회
Revision 0회

──────────────────────────────────────────────────────────────

AI 최종 검수

Critic Result     PASS

──────────────────────────────────────────────────────────────

운영 담당자 최종 승인

AI 기획 및 검수가 완료되었습니다.

[ 승인 ]       [ 수정 요청 ]       [ 거절 ]
```
즉 Page 2는 **이벤트 기본 정보 → Day 1\~7 전체 기획안 → 게임 규칙 검증 → AutoHealing → Critic → Human Approval** 순서로 하나의 긴 결과 보고서처럼 세로 스크롤하는 구조로 구현합니다.
## 16-2. 구현 원칙
- 이벤트 결과는 Page 2에서 가장 큰 비중을 차지합니다.
- Day 1\~7은 접거나 표로 축약하지 않고 기본 상태에서 모두 노출합니다.
- `이벤트 기본 정보`는 하나의 큰 요약 Card로 표시합니다.
- Day별 결과는 동일한 디자인의 반복 Card로 표시합니다.
- Backend 데이터는 `missions[]`와 `rewards[]`로 분리해서 받고, Frontend에서 같은 `day` 값을 기준으로 묶어 한 Day Card에 표시합니다.
- 한 Day에 Mission 또는 Reward가 여러 개면 모두 반복 표시합니다.
- 게임 규칙 검증, AutoHealing, Critic, Human Approval은 이벤트 기획안 바로 아래에 이어서 표시합니다.
- 기술적인 Trace 원문은 Page 2에서 과도하게 강조하지 않고 Page 3 운영·디버그에서 상세 확인합니다.
- Frontend는 이벤트 기획 문구나 ID→이름 변환을 새로 만들지 않고 Backend가 제공한 값을 표시합니다.
## 16-3. Backend에서 필요한 결과 필드
### MVP 필수
```plain text
event_name
target_user
duration
objective

missions[].day
missions[].mission_name
missions[].content_id
missions[].content_name
missions[].mission_type
missions[].target_count

rewards[].day
rewards[].item_id
rewards[].item_name
rewards[].quantity
```
### Optional 표시 필드
```plain text
event_concept
operation_summary
missions[].day_title
missions[].planning_reason
```
Optional 필드는 `null`을 허용합니다. 값이 없으면 Frontend는 해당 UI 영역을 숨기며, Optional 값이 없다는 이유로 화면 오류나 Workflow 실패로 처리하지 않습니다.
`objective`는 AI가 재작성한 문구가 아니라 **사용자가 입력한 원문**을 표시합니다.
예시:
```json
{
  "event_name": "신규 유저 7일 성장 이벤트",
  "target_user": "new_user",
  "duration": 7,
  "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
  "event_concept": null,
  "operation_summary": null,
  "missions": [
    {
      "day": 1,
      "day_title": null,
      "mission_name": "일반 사냥 20회",
      "content_id": "HUNT_001",
      "content_name": "일반 사냥",
      "mission_type": "monster_kill",
      "target_count": 20,
      "planning_reason": null
    }
  ],
  "rewards": [
    {
      "day": 1,
      "item_id": "ITEM_001",
      "item_name": "회복 포션",
      "quantity": 10
    }
  ]
}
```
## 16-4. 게임 규칙 검증 데이터
게임 규칙 검증 영역은 Backend의 `rule_validation` 값을 사용합니다.
```plain text
rule_validation.is_valid
rule_validation.error_type
rule_validation.reason
rule_validation.evidence
rule_validation.game_data_version
rule_validation.data_source
```
MVP에서는 `checks[]`를 필수로 요구하지 않습니다. Frontend는 `evidence`를 검증 근거로 표시하고, 직접 재계산하거나 판정하지 않습니다.
---
# 17. Workflow 상태 표시
Backend의 상태값은 Frontend에서 사용자가 이해하기 쉬운 표현으로 변환하여 표시합니다.
### Workflow Status
<table header-row="true">
<tr>
<td>Backend</td>
<td>화면</td>
</tr>
<tr>
<td>RUNNING</td>
<td>실행 중</td>
</tr>
<tr>
<td>RETRYING</td>
<td>자동 복구 중</td>
</tr>
<tr>
<td>WAITING_APPROVAL</td>
<td>승인 대기</td>
</tr>
<tr>
<td>COMPLETED</td>
<td>완료</td>
</tr>
<tr>
<td>FAILED</td>
<td>실행 실패</td>
</tr>
<tr>
<td>CANCELLED</td>
<td>운영자 취소</td>
</tr>
</table>
### Critic Status
<table header-row="true">
<tr>
<td>Backend</td>
<td>화면</td>
</tr>
<tr>
<td>PASS</td>
<td>검수 통과</td>
</tr>
<tr>
<td>RETRY</td>
<td>수정 후 재검수</td>
</tr>
<tr>
<td>FAIL</td>
<td>검수 실패</td>
</tr>
</table>
### Approval Status
<table header-row="true">
<tr>
<td>Backend</td>
<td>화면</td>
</tr>
<tr>
<td>PENDING</td>
<td>승인 대기</td>
</tr>
<tr>
<td>APPROVED</td>
<td>승인 완료</td>
</tr>
<tr>
<td>REVISION_REQUESTED</td>
<td>수정 요청</td>
</tr>
<tr>
<td>REJECTED</td>
<td>거절</td>
</tr>
</table>
`workflow_status`, `critic_status`, `approval_status`는 서로 다른 의미를 가지기 때문에 각각 별도로 표시합니다.
---
# 18. Human Approval
Critic이 PASS했다고 해서 자동으로 이벤트를 최종 확정하지 않습니다.
다음 상태일 때 Human Approval 영역을 활성화합니다.
```plain text
workflow_status = WAITING_APPROVAL

approval_status = PENDING

critic_status = PASS
```
화면에서는 다음 3가지 기능을 제공합니다.
```plain text
[ 승인 ]

[ 수정 요청 ]

[ 거절 ]
```
---
# 19. 승인
사용자가 승인 버튼을 선택하면 현재 이벤트 기획안을 최종 확정합니다.
```plain text
APPROVE
↓
approval_status = APPROVED
↓
workflow_status = COMPLETED
↓
final_result 확정
```
Frontend에서는 다음 메시지를 표시합니다.
```plain text
✓ 이벤트 기획안이 최종 승인되었습니다.
```
---
# 20. 수정 요청
사용자가 수정 요청을 선택하면 수정 내용을 작성할 수 있는 입력 영역을 제공합니다.
예:
```plain text
수정 요청 내용

Day 1~3 미션을 전투보다
성장 중심으로 변경해주세요.

[ 수정 요청 보내기 ]
```
수정 요청 후 Backend에서는 다음 Workflow를 다시 실행합니다.
```plain text
운영자 수정 요청
↓
Event Planner
↓
EventPlanResult Validation
↓
Balance & Rule
↓
Critic
↓
PASS
↓
새 결과 생성
```
새 결과가 생성되면 Result Version이 증가합니다.
```plain text
v1
↓
v2
```
Human 수정 요청은 AutoHealing의 `revision_count`에 포함하지 않습니다.
Trace에는 다음과 같이 별도로 기록합니다.
```plain text
human_revision
```
---
# 21. 거절
운영자가 현재 이벤트를 사용하지 않을 경우 거절할 수 있습니다.
```plain text
REJECT
↓
approval_status = REJECTED
↓
workflow_status = CANCELLED
```
`CANCELLED`는 시스템 오류로 발생하는 `FAILED`와 구분하여 표시합니다.
---
# 22. Page 3. 운영 · 디버그
## 22-1. 화면 목적
현재 생성한 Task가 실제로 어떤 Agent / Supervisor / Tool 경로를 거쳤는지 확인하는 기술 확인 화면입니다.
이번 MVP에서는 Page 3를 **Trace 중심으로 단순화**합니다.
```plain text
① 현재 Task 정보
② Backend Health
③ 상세 Trace
④ Trace 안에서 Tool Retry / Fallback 확인
```
Task 목록, 별도 게임 데이터 조회, 종합 시스템 상태, 별도 MCP Tool Log는 후속 확장 기능으로 둡니다.
---
# 23. 현재 Task 정보
현재 Frontend가 보유한 `task_id`를 표시합니다.
```plain text
Task ID
TASK-001

Workflow Status
WAITING_APPROVAL

Result Version
1
```
이번 MVP에는 `GET /api/tasks` 목록 API가 없으므로 이전 Task 선택 UI는 제공하지 않습니다.
---
# 24. Backend Health
Backend 자체 상태는 아래 두 API로 확인합니다.
```plain text
GET /health/live
GET /health/ready
```
화면 예:
```plain text
Backend Live
✓ OK

Backend Ready
✓ READY
```
MCP / PostgreSQL / OpenAI를 한 번에 조회하는 `GET /api/system/health`는 이번 MVP에서 사용하지 않습니다.
---
# 25. 상세 Trace
현재 `task_id`를 기준으로 Workflow 실행 기록을 확인합니다.
```plain text
GET /api/tasks/{task_id}/trace
```
Trace 기본 표:
```plain text
sequence
time
actor
action
status
error_type
reason
```
상세 보기에서 필요 시 다음 정보를 추가로 표시합니다.
```plain text
actor_id
policy_decision
reason_code
failed_tool
fallback_tool
data_source
game_data_version
retry_count
revision_count
result_version
```
예:
```plain text
14:23:19  BalanceRule   validate_plan        ERROR
14:23:20  Supervisor    RUN_AGENT            SUCCESS
14:23:25  EventPlanner  regenerate_plan      SUCCESS
14:23:29  MCP           get_game_rules       SUCCESS
14:23:31  BalanceRule   validate_plan        SUCCESS
14:23:35  Critic        final_review         PASS
```
첫 유효한 `current_result`가 저장되기 전 Trace의 `result_version`은 `0`이고, 첫 Critic PASS 후 저장되면 `1`이 됩니다.
---
# 26. Tool Retry / Fallback 확인
별도 `GET /api/tasks/{task_id}/tool-logs`는 사용하지 않습니다.
MCP Tool 실행과 복구 과정은 Trace에서 다음처럼 확인합니다.
```plain text
MCP          get_game_rules             ERROR
Supervisor   TOOL_FALLBACK              SUCCESS
MCP          get_game_rules_fallback    SUCCESS
BalanceRule  validate_plan              SUCCESS
```
또는 Timeout Retry:
```plain text
MCP          get_game_rules    ERROR
Supervisor   RETRY             RETRYING
MCP          get_game_rules    SUCCESS
```
Frontend에서는 API Key, 전체 Prompt 원문 등 민감정보를 표시하지 않습니다.
---
# 27. Backend API 연동
이번 MVP에서 Frontend가 사용하는 Backend API는 다음과 같습니다.
<table header-row="true">
<tr>
<td>기능</td>
<td>Method</td>
<td>Endpoint</td>
</tr>
<tr>
<td>Normal Mode 이벤트 생성</td>
<td>POST</td>
<td>`/api/events/generate`</td>
</tr>
<tr>
<td>Test Mode 이벤트 생성</td>
<td>POST</td>
<td>`/api/test/events/generate`</td>
</tr>
<tr>
<td>Task 상태 조회</td>
<td>GET</td>
<td>`/api/tasks/{task_id}`</td>
</tr>
<tr>
<td>Human Approval</td>
<td>POST</td>
<td>`/api/tasks/{task_id}/approval`</td>
</tr>
<tr>
<td>Trace 조회</td>
<td>GET</td>
<td>`/api/tasks/{task_id}/trace`</td>
</tr>
<tr>
<td>Backend Live Check</td>
<td>GET</td>
<td>`/health/live`</td>
</tr>
<tr>
<td>Backend Ready Check</td>
<td>GET</td>
<td>`/health/ready`</td>
</tr>
</table>
이번 MVP에서 사용하지 않는 후속 API:
```plain text
GET /api/tasks
GET /api/tasks/{task_id}/tool-logs
GET /api/game-data
GET /api/system/health
```
Human Approval 요청:
```plain text
task_id
actor_id
result_version
action
approval_comment
```
`approval_comment`는 `REQUEST_REVISION`일 경우에만 필수입니다.
Frontend API Client Timeout:
- 이벤트 생성 / Test Mode / REQUEST_REVISION: 180초
- 일반 GET API: 10초
- APPROVE / REJECT: 30초
HTTP Timeout 또는 네트워크 오류 후 생성 요청을 재시도할 때는 **기존 idempotency_key를 그대로 재사용**합니다.
---
# 28. Streamlit 상태 관리
Frontend의 화면 이동 및 사용자 상태는 `st.session_state`를 사용하여 관리합니다.
주요 상태값:
```plain text
logged_in
actor_id

selected_mode
fault_type

task_id

workflow_status
critic_status
approval_status

result_version

current_result
final_result
rule_validation
autohealing_events

is_loading
last_error
```
이번 MVP에서는 Task 목록 선택 기능을 사용하지 않으므로 `selected_task_id`는 관리하지 않습니다.
페이지 새로고침 또는 재접속 후 `task_id`가 유지되어 있다면 `GET /api/tasks/{task_id}`로 최신 상태를 다시 조회합니다.
로그아웃 또는 새로운 Workflow 실행 시 필요한 상태값을 초기화합니다.
---
# 29. 오류 처리
Frontend는 **HTTP/API 오류**와 **Workflow FAILED(Safe Stop)**를 구분하여 처리합니다.
## 29-1. API 호출 자체 실패
Backend 연결 실패 등으로 Task가 생성되지 않은 경우 다음과 같이 안내합니다.
```plain text
이벤트 생성 요청에 실패했습니다.

Backend 서버 연결 상태를 확인해주세요.

[ 다시 시도 ]
```
## 29-2. Workflow FAILED / Safe Stop
Workflow가 생성된 뒤 Retry 한도 초과 등으로 `FAILED`가 된 Task는 기존 Task를 이어서 재실행하지 않습니다.
### Page 2 실패 결과 화면 예시
```plain text
신규 유저 7일 성장 이벤트

[ 실행 실패 ]          Workflow FAILED

Workflow

✓ Event Planner
        ↓
! Balance & Rule
        ↓
! MCP Timeout
        ↓
↻ Retry 1   FAILED
        ↓
↻ Retry 2   FAILED
        ↓
↻ Retry 3   FAILED
        ↓
✕ Safe Stop
```
결과 영역은 다음처럼 표시합니다.
```plain text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Workflow가 안전 종료되었습니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Error Type
MCP_TIMEOUT

복구 시도
3회

최종 상태
FAILED

검증되지 않은 이벤트 결과는
최종 기획안으로 사용할 수 없습니다.

[ Trace 확인 ]
[ 새 이벤트 요청 만들기 ]
```
`FAILED` 상태에서는 승인 관련 기능을 활성화하지 않습니다.
```plain text
운영 담당자 최종 승인

승인할 수 없습니다.
Workflow가 정상적으로 완료되지 않았습니다.

[ 승인 ]        비활성화
[ 수정 요청 ]   비활성화
[ 거절 ]        비활성화
```
MCP Server 등 의존 서비스가 복구되더라도 기존 FAILED Task를 재개하지 않고 **새 요청 → 새 task_id**로 다시 실행합니다.
## 29-3. 중간 오류 발생 후 AutoHealing 성공 화면
중간 단계에서 오류가 발생했지만 AutoHealing으로 복구된 경우에는 최종 결과를 정상적으로 표시하고, 오류와 복구 과정을 별도 요약 영역으로 강조합니다.
```plain text
신규 유저 7일 성장 이벤트              Result v1

[ 승인 대기 ]   [ Critic PASS ]   [ AutoHealing 1회 ]

Workflow

✓ Event Planner
        ↓
! Balance & Rule
  mission_unreachable
        ↓
✓ AutoHealing
  Event Planner 재실행
        ↓
✓ 재검증 PASS
        ↓
✓ Critic PASS
        ↓
● Human Approval
```
```plain text
AutoHealing 결과

수정 전
Day 7
레이드 보스 1회 처치
필요 레벨 Lv.30

신규 유저 예상 레벨 Lv.15

        ↓

자동 수정

        ↓

수정 후
Day 7
일반 던전 3회 클리어
필요 레벨 Lv.10

✓ 재검증 PASS

Revision 1회
Retry 0회
```
이 경우에는 수정된 최종 이벤트 기획안을 정상적으로 보여주고 `Critic PASS + WAITING_APPROVAL + PENDING`이면 승인 기능을 활성화합니다.
상세한 `실패 → 복구 결정 → Agent 재실행 → 재검증 → PASS` 실행 순서는 Page 3의 Trace에서 확인합니다.
## 29-4. 실패/복구 결과 표시 원칙
- Page 2: 운영자가 이해하기 쉬운 오류 요약, 수정 전/후, 최종 결과 표시
- Page 3: 실제 Agent / Supervisor / Tool 실행 순서를 Trace로 표시
- 복구 성공: 수정된 이벤트 결과 + AutoHealing 요약 + 승인 가능
- 복구 실패: 최종 기획안 미확정 + Safe Stop 화면 + 승인 불가
- 검증 실패 중간 산출물은 최종 이벤트 기획안처럼 표시하지 않음
- `FAILED` Task는 Trace 확인 후 새 이벤트 요청으로 이동
## 29-5. Error Code별 Frontend 처리
<table header-row="true">
<tr>
<td>Error Code</td>
<td>Frontend 처리</td>
</tr>
<tr>
<td>`INPUT_VALIDATION_ERROR`</td>
<td>해당 입력값 오류 표시</td>
</tr>
<tr>
<td>`FAULT_INJECTION_DISABLED`</td>
<td>Test Mode 사용 불가 안내</td>
</tr>
<tr>
<td>`STALE_RESULT_VERSION`</td>
<td>`detail.current_result_version` 확인 후 현재 Task를 재조회하여 화면 갱신</td>
</tr>
<tr>
<td>`TASK_NOT_FOUND`</td>
<td>현재 Task를 찾을 수 없음을 안내하고 새 이벤트 요청으로 유도</td>
</tr>
<tr>
<td>`IDEMPOTENCY_KEY_CONFLICT`</td>
<td>중복 요청 충돌 안내</td>
</tr>
<tr>
<td>`MAX_RETRIES_EXCEEDED`</td>
<td>Safe Stop 화면 + Trace 확인</td>
</tr>
<tr>
<td>`INTERNAL_SERVER_ERROR`</td>
<td>일반 서버 오류 안내</td>
</tr>
</table>
검증되지 않은 이벤트 결과는 최종 결과처럼 표시하지 않습니다.
---
# 30. Frontend 디렉터리 구조
```plain text
frontend/
│
├─ app.py
│
├─ pages/
│  ├─ 1_event_create.py
│  ├─ 2_event_result.py
│  └─ 3_ops_debug.py
│
├─ components/
│  ├─ event_form.py
│  ├─ workflow_status.py
│  ├─ event_result.py
│  ├─ autohealing_view.py
│  └─ trace_viewer.py
│
├─ services/
│  └─ backend_client.py
│
├─ .env
├─ .env.docker
├─ .env.example
├─ .env.docker.example
│
├─ requirements.txt
├─ .dockerignore
└─ Dockerfile
```
---
# 31. 파일별 역할
### `app.py`
- Demo Login
- Session State 초기화
- 공통 설정
### `1_event_create.py`
- Normal / Test Mode 선택
- 이벤트 조건 입력
- 장애 주입 선택
- 이벤트 생성 API 호출
### `2_event_result.py`
- Workflow 진행 상태
- Workflow 실행 기록
- AutoHealing 결과
- 이벤트 기획안
- Human Approval
### `3_ops_debug.py`
- 현재 Task 정보
- Backend Live / Ready 상태
- 상세 Trace
- Trace 기반 Tool Retry / Fallback 확인
### `event_form.py`
이벤트 생성 입력 Form을 구성합니다.
### `workflow_status.py`
Agent 실행 단계와 Workflow 상태를 표시합니다.
### `event_result.py`
최종 이벤트 기획안을 **이벤트 기본 정보 + Day별 카드가 이어지는 세로 스크롤형 결과 보고서**로 표시합니다.
Backend의 `missions[]`와 `rewards[]`를 `day` 기준으로 묶어 표시하며, Optional 표시 필드는 값이 있을 때만 노출합니다.
정상 결과, AutoHealing 복구 성공 결과, Workflow FAILED 결과에 따라 화면 구성을 분기합니다.
### `autohealing_view.py`
오류 발생 → AutoHealing → 복구 결과를 표시합니다.
### `trace_viewer.py`
Trace와 Agent 실행 기록을 표시합니다.
### `backend_client.py`
FastAPI Backend와 통신하는 공통 기능을 관리합니다.
주요 책임:
- Backend URL 환경변수 로드
- Event Generate / Test Generate
- Task 조회
- Approval
- Trace 조회
- Live / Ready Health Check
- API Timeout 적용
- 공통 Error Response 처리
- `STALE_RESULT_VERSION.detail.current_result_version` 처리
- 이벤트 생성용 UUID `idempotency_key` 생성 및 재요청 시 동일 Key 재사용
### 요청 중 중복 클릭 방지
이벤트 생성, 수정 요청, 승인 처리 중에는 `is_loading=true`로 관리하고 관련 버튼을 비활성화합니다. 이벤트 생성 요청은 UUID 기반 `idempotency_key`를 함께 전달하여 더블 클릭이나 네트워크 재전송으로 인한 중복 Task 생성을 방지합니다.
---
# 32. Frontend 구현 우선순위
### 1단계
화면 기본 구조 구현
```plain text
Login
Sidebar
3개 Page
```
### 2단계
이벤트 생성 Form 구현
```plain text
Normal / Test Mode
Event Name
Target User
Duration
Objective
Fault Type
```
### 3단계
Backend API 연결
```plain text
Event Generate
Test Event Generate
Task 조회
Approval
Trace
Live / Ready
```
### 4단계
결과 화면 구현
```plain text
Workflow Status
Event Result
Validation Result
```
### 5단계
AutoHealing 화면 구현
```plain text
오류
↓
복구 과정
↓
수정 결과
```
### 6단계
Human Approval 구현
```plain text
Approve
Request Revision
Reject
```
### 7단계
운영·디버그 화면 구현
```plain text
Current Task
Backend Live / Ready
Trace
Tool Retry / Fallback Trace
```
### 8단계
Frontend Docker / CI/CD 구성
---
# 33. Frontend 완료 기준
다음 조건을 만족하면 Frontend MVP 구현이 완료된 것으로 봅니다.
- [ ] Demo 직원 ID로 로그인할 수 있다.
- [ ] 로그인 상태가 Streamlit Session State에 유지된다.
- [ ] 이벤트 생성 / 진행 상태·결과 / 운영·디버그 3개 페이지를 이동할 수 있다.
- [ ] 이벤트명, 대상, 기간, 목적을 입력할 수 있다.
- [ ] 이벤트 기간은 1\~7일로 제한된다.
- [ ] Normal Mode와 Test Mode를 선택할 수 있다.
- [ ] Test Mode에서 장애 유형을 선택할 수 있다.
- [ ] Backend 이벤트 생성 API를 호출할 수 있다.
- [ ] API 처리 중 Loading 상태를 표시한다.
- [ ] Backend의 `missions[]`와 `rewards[]`를 day 기준으로 묶어 Day 카드에 표시할 수 있다.
- [ ] 한 Day에 여러 Mission / Reward가 있으면 모두 표시할 수 있다.
- [ ] `event_concept / operation_summary / day_title / planning_reason`이 null이면 해당 UI를 숨길 수 있다.
- [ ] `objective`는 사용자 입력 원문을 표시한다.
- [ ] Workflow의 Agent 실행 단계를 확인할 수 있다.
- [ ] Workflow 실행 기록을 시간순으로 확인할 수 있다.
- [ ] MCP Tool Retry / Fallback 기록을 별도 Tool Log가 아니라 Trace에서 확인할 수 있다.
- [ ] AutoHealing 발생 여부를 확인할 수 있다.
- [ ] AutoHealing 발생 시 오류 원인과 수정 전·후 결과를 확인할 수 있다.
- [ ] 한 Workflow에서 복수 AutoHealing이 발생한 경우 `autohealing_events[]`를 순서대로 표시할 수 있다.
- [ ] `rule_validation.reason / evidence / game_data_version / data_source`를 확인할 수 있다.
- [ ] 생성된 이벤트 기획안을 세로 스크롤형 Day 카드로 확인할 수 있다.
- [ ] AutoHealing 복구 성공 시 수정 전/후와 재검증 PASS를 Page 2에서 확인할 수 있다.
- [ ] Workflow FAILED 시 Safe Stop 전용 화면이 표시되고 승인 기능이 비활성화된다.
- [ ] HTTP/API 오류와 Workflow FAILED를 서로 다른 화면 흐름으로 처리한다.
- [ ] workflow_status / critic_status / approval_status를 각각 확인할 수 있다.
- [ ] 첫 유효 결과 저장 전 result_version=0, 첫 결과 저장 후 Result v1 규칙을 따른다.
- [ ] WAITING_APPROVAL + PENDING + PASS일 때만 승인 기능이 활성화된다.
- [ ] 수정 요청 내용을 입력할 수 있다.
- [ ] 이벤트를 거절할 수 있다.
- [ ] FAILED와 CANCELLED를 구분하여 표시한다.
- [ ] 현재 Task ID 기준 Trace를 조회할 수 있다.
- [ ] Backend `/health/live`, `/health/ready` 상태를 확인할 수 있다.
- [ ] API Key 및 전체 Prompt와 같은 민감정보를 Frontend에 표시하지 않는다.
- [ ] Backend 연결 실패 시 사용자에게 오류 안내를 제공한다.
- [ ] `STALE_RESULT_VERSION` 발생 시 `detail.current_result_version`을 확인하고 최신 Task를 재조회한다.
- [ ] `FAILED` Task는 기존 Task 재개가 아닌 새 요청으로 유도한다.
- [ ] HTTP Timeout 재요청 시 같은 `idempotency_key`를 재사용한다.
- [ ] API 요청 중 버튼 중복 클릭을 방지한다.
- [ ] Frontend Docker Image를 생성할 수 있다.
- [ ] Frontend CI/CD를 통해 AWS EC2에 배포할 수 있다.
### MVP 후속 확장
다음은 이번 완료 기준에 포함하지 않습니다.
```plain text
최근 Task 목록 선택
별도 MCP Tool Log 화면
게임 데이터 조회 API 화면
Backend / MCP / PostgreSQL / OpenAI 종합 Health 화면
```
---
# 34. Frontend 테스트 시나리오
<table header-row="true">
<tr>
<td>ID</td>
<td>테스트</td>
<td>기대 결과</td>
</tr>
<tr>
<td>FE-01</td>
<td>Demo Login 성공 / 실패</td>
<td>허용된 Demo ID만 운영 화면 진입</td>
</tr>
<tr>
<td>FE-02</td>
<td>필수 입력 누락 / 기간 범위 오류</td>
<td>이벤트 생성 요청 전 입력 오류 표시</td>
</tr>
<tr>
<td>FE-03</td>
<td>Normal Mode 이벤트 생성</td>
<td>WAITING_APPROVAL 또는 FAILED Task Response 정상 표시</td>
</tr>
<tr>
<td>FE-04</td>
<td>Test Mode 장애 선택 및 실행</td>
<td>선택한 fault_type 전달 및 복구 결과 표시</td>
</tr>
<tr>
<td>FE-05</td>
<td>missions / rewards 분리 Response</td>
<td>day 기준으로 묶어 같은 Day 카드에 표시</td>
</tr>
<tr>
<td>FE-06</td>
<td>Optional 표시 필드 null</td>
<td>해당 UI만 숨기고 나머지 결과 정상 표시</td>
</tr>
<tr>
<td>FE-07</td>
<td>Rule Validation</td>
<td>reason / evidence / version / source 표시</td>
</tr>
<tr>
<td>FE-08</td>
<td>AutoHealing 결과</td>
<td>오류 원인, 수정 전·후, 재검증 결과 표시</td>
</tr>
<tr>
<td>FE-09</td>
<td>Human Approval 활성 조건</td>
<td>WAITING_APPROVAL + PENDING + PASS일 때만 활성화</td>
</tr>
<tr>
<td>FE-10</td>
<td>REQUEST_REVISION</td>
<td>새 current_result와 증가한 result_version 표시</td>
</tr>
<tr>
<td>FE-11</td>
<td>STALE_RESULT_VERSION</td>
<td>detail.current_result_version 확인 후 현재 Task 재조회</td>
</tr>
<tr>
<td>FE-12</td>
<td>Workflow FAILED</td>
<td>HTTP 오류와 구분하여 Safe Stop + Trace + 새 요청 표시</td>
</tr>
<tr>
<td>FE-13</td>
<td>Primary Tool 실패 → Fallback</td>
<td>Trace에서 Tool Fallback 경로 확인</td>
</tr>
<tr>
<td>FE-14</td>
<td>MCP Timeout → Retry 성공</td>
<td>Trace에서 ERROR → RETRY → SUCCESS 순서 확인</td>
</tr>
<tr>
<td>FE-15</td>
<td>Backend 연결 실패 / HTTP Timeout</td>
<td>Task 실패 화면과 다른 API 오류 안내 표시</td>
</tr>
<tr>
<td>FE-16</td>
<td>HTTP Timeout 후 생성 재요청</td>
<td>같은 idempotency_key 사용</td>
</tr>
<tr>
<td>FE-17</td>
<td>Backend Health</td>
<td>live / ready 상태 표시</td>
</tr>
</table>
CI/CD의 Frontend Smoke Check:
- Streamlit 앱 정상 기동
- 메인 페이지 오류 없이 로드
- 필수 Python Module Import 성공
- 환경변수 누락 시 Secret 미노출 + 명확한 오류
- Docker Image 실행 후 Streamlit Port 정상 응답
---
# 35. Frontend 핵심 요약
본 Frontend는 단순한 AI 이벤트 생성 화면이 아니라 **이벤트 생성부터 Agent 검수, AutoHealing, Human Approval까지 전체 Workflow를 운영 담당자가 확인할 수 있는 관리 화면**을 목표로 합니다.
화면은 구현 난이도와 발표 흐름을 고려하여 다음 **3개의 핵심 Page로 최소화**합니다.
```plain text
① 이벤트 생성

사용자가 무엇을 요청했는가
        ↓

② 진행 상태 / 결과

AI Agent가 어떻게 처리했고
어떤 오류를 발견했으며
어떻게 AutoHealing 했는가
        ↓

③ 운영 · 디버그

현재 Task에서 어떤 Agent와 Tool이 실행되었고
Retry / Fallback이 어떻게 동작했는가
```
### Frontend의 핵심 한 문장
> **운영 담당자가 AI 이벤트 생성부터 검수·자동복구·최종 승인까지 전체 과정을 한눈에 확인하고 제어할 수 있는 Streamlit 기반 운영 UI를 구현합니다.**
---
# 36. 첨부 화면 시안 반영 기준과 Backend 의존성
이 절은 3개 Page의 시각적 구성을 구체화합니다. 기존 MVP 기능·API 범위는 유지하며, API 계약서 40절의 추가 계약 후보는 구현 전까지 동적 기능으로 표시하지 않습니다.
## 36-1. 공통 화면
- 기존 Demo Login 이후 짙은 남색 사이드바, 상단 제목·사용자 영역, 흰색 카드와 파란색 강조색을 공통 스타일로 적용합니다.
- 데스크톱에서는 본문과 우측 보조 카드의 2열 구성을 사용하고, 좁은 화면에서는 세로 배치합니다.
- 상태 색상은 성공·대기·오류·복구를 구분하되 텍스트와 함께 표시합니다.
## 36-2. Page 1 이벤트 생성
- 시안의 기본 정보 카드에는 계약된 `event_name`, `target_user`, `duration`(1\~7), `objective`를 배치합니다.
- 추가 설정 입력은 현재 생성 API에 없으므로 사용자에게 저장·반영되는 입력처럼 제공하지 않습니다. 실제 생성에 반영하려면 API 계약서 40-2절의 요청 확장이 선행되어야 합니다.
- 참고 가이드는 정적 설명으로 표시할 수 있습니다. ‘예시 불러오기’는 1\~7일 범위의 정적 샘플로 제공할 수 있으나 실제 최근 Task라는 표현은 사용하지 않습니다.
- MCP 연결 상태 및 콘텐츠·아이템·레벨·규칙 건수는 실제 데이터 API가 준비된 후 표시합니다. Backend Ready만으로 개별 수치를 표시하지 않습니다.
## 36-3. Page 2 진행 상태 / 결과
- Task Response와 Trace로 단계 이력을 재구성하고, Day별 Mission·Reward, Rule Validation, AutoHealing 전후, Critic, 승인 버튼을 시안의 카드 구조에 배치합니다.
- 동기 생성 요청 중에는 로딩 상태를 표시하고, 응답 전 실시간 Agent 진행 단계는 표시하지 않습니다.
- 개별 검증 PASS 목록은 Backend가 검사별 결과를 제공할 때 추가합니다. 현행 응답에서는 전체 검증 결과·근거·버전을 표시합니다.
- 승인·수정 요청·거절은 기존 상태 및 `result_version` 조건을 지킵니다. FAILED Task는 Safe Stop과 Trace 진입을 표시합니다.
## 36-4. Page 3 운영 · 디버그
- 현재 `task_id`의 요약, 상세 Trace, Trace에서 필터링한 MCP/Backend/Database 실행 기록, `/health/live`·`/health/ready`를 배치합니다.
- 정확한 실행 시간, 원 요청의 영속 조회, 개별 MCP 상태, 원시 시스템 로그는 해당 계약과 Backend 구현 전까지 수치나 로그를 만들어 표시하지 않습니다.
## 36-5. 구현 의존성
현재 저장소의 `backend/app/api/event.py`와 `backend/app/api/health.py`는 라우트 구현 전 상태입니다. Frontend는 계약서 예시 응답으로 화면을 개발할 수 있으나 실제 생성·승인·조회 연동 완료는 Backend 엔드포인트와 통합 시험으로 확인합니다.

