# Frontend_Backend_API_Contract

> 노션 원문: https://app.notion.com/p/3f281f16897680b8a32dcb2db449328b
> 노션 최종 수정: 2026-10-07T10:20:35.929Z

# 1.  문서 목적
본 문서는 \*\*게임 라이브 이벤트 기획·검수 AutoHealing 시스템\*\*의<br>Frontend(Streamlit)와 Backend(FastAPI) 간 API 연동 규칙을 정의합니다.<br><br>Frontend는 본 문서에 정의된 API와 Response 구조를 기준으로 화면을 구현하며,<br>Backend는 해당 Contract에 맞춰 데이터를 제공합니다.
# 2. 기본 통신 구조
\`\`\`text<br>Streamlit Frontend<br>        ↓<br>FastAPI Backend<br>        ↓<br>Supervisor Orchestrator<br>        ↓<br>Agent / MCP / PostgreSQL / OpenAI
	Frontend는 다음 시스템에 직접 접근하지 않습니다.
```plain text
OpenAI API
MCP Server
PostgreSQL
```
모든 데이터 요청과 Workflow 실행은 FastAPI Backend를 통해 처리합니다.
---
# 3. API 목록
이번 MVP에서 Frontend가 사용하는 API는 아래 **핵심 API만** 포함합니다.
<table header-row="true">
<tr>
<td>구분</td>
<td>Method</td>
<td>Endpoint</td>
<td>용도</td>
</tr>
<tr>
<td>일반 이벤트 생성</td>
<td>POST</td>
<td>`/api/events/generate`</td>
<td>Normal Mode 이벤트 생성</td>
</tr>
<tr>
<td>Test 이벤트 생성</td>
<td>POST</td>
<td>`/api/test/events/generate`</td>
<td>Fault Injection 기반 테스트</td>
</tr>
<tr>
<td>Task 조회</td>
<td>GET</td>
<td>`/api/tasks/{task_id}`</td>
<td>Workflow 상태 및 결과 조회</td>
</tr>
<tr>
<td>승인 처리</td>
<td>POST</td>
<td>`/api/tasks/{task_id}/approval`</td>
<td>승인 / 수정 요청 / 거절</td>
</tr>
<tr>
<td>Trace 조회</td>
<td>GET</td>
<td>`/api/tasks/{task_id}/trace`</td>
<td>Agent / Supervisor / Tool 실행 기록 조회</td>
</tr>
<tr>
<td>Live Check</td>
<td>GET</td>
<td>`/health/live`</td>
<td>Backend Process 실행 여부</td>
</tr>
<tr>
<td>Ready Check</td>
<td>GET</td>
<td>`/health/ready`</td>
<td>Backend 서비스 준비 여부</td>
</tr>
</table>
다음 API는 **이번 MVP 범위에서 제외하고 후속 확장 기능**으로 둡니다.
```plain text
GET /api/tasks
GET /api/tasks/{task_id}/tool-logs
GET /api/game-data
GET /api/system/health
```
Tool 호출 내역은 별도 Tool Log API 대신 Trace에서 확인합니다.
---
# 4. 공통 Enum
## 4-1. target_user
```plain text
new_user
returning_user
all_user
```
<table header-row="true">
<tr>
<td>값</td>
<td>의미</td>
</tr>
<tr>
<td>`new_user`</td>
<td>신규 유저</td>
</tr>
<tr>
<td>`returning_user`</td>
<td>복귀 유저</td>
</tr>
<tr>
<td>`all_user`</td>
<td>전체 유저</td>
</tr>
</table>
---
## 4-2. workflow_status
```plain text
RUNNING
RETRYING
WAITING_APPROVAL
COMPLETED
FAILED
CANCELLED
```
<table header-row="true">
<tr>
<td>값</td>
<td>의미</td>
</tr>
<tr>
<td>`RUNNING`</td>
<td>Workflow 실행 중</td>
</tr>
<tr>
<td>`RETRYING`</td>
<td>기술 장애 자동 복구 중</td>
</tr>
<tr>
<td>`WAITING_APPROVAL`</td>
<td>Critic PASS 후 운영자 승인 대기</td>
</tr>
<tr>
<td>`COMPLETED`</td>
<td>최종 승인 완료</td>
</tr>
<tr>
<td>`FAILED`</td>
<td>복구 실패 / Safe Stop</td>
</tr>
<tr>
<td>`CANCELLED`</td>
<td>운영자 거절</td>
</tr>
</table>
---
## 4-3. critic_status
```plain text
PASS
RETRY
FAIL
null
```
<table header-row="true">
<tr>
<td>값</td>
<td>의미</td>
</tr>
<tr>
<td>`PASS`</td>
<td>검수 통과</td>
</tr>
<tr>
<td>`RETRY`</td>
<td>수정 후 재검수 필요</td>
</tr>
<tr>
<td>`FAIL`</td>
<td>Critic 검수 실패</td>
</tr>
<tr>
<td>`null`</td>
<td>Critic 실행 전</td>
</tr>
</table>
---
## 4-4. approval_status
```plain text
PENDING
APPROVED
REVISION_REQUESTED
REJECTED
null
```
<table header-row="true">
<tr>
<td>값</td>
<td>의미</td>
</tr>
<tr>
<td>`PENDING`</td>
<td>승인 대기</td>
</tr>
<tr>
<td>`APPROVED`</td>
<td>승인 완료</td>
</tr>
<tr>
<td>`REVISION_REQUESTED`</td>
<td>운영자 수정 요청</td>
</tr>
<tr>
<td>`REJECTED`</td>
<td>운영자 거절</td>
</tr>
<tr>
<td>`null`</td>
<td>승인 단계 이전</td>
</tr>
</table>
---
## 4-5. approval action
```plain text
APPROVE
REQUEST_REVISION
REJECT
```
---
# 5. Normal Mode 이벤트 생성
## Endpoint
```plain text
POST /api/events/generate
```
## Request
```plain text
{
  "actor_id": "liveops01",
  "event_name": "신규 유저 7일 성장 이벤트",
  "target_user": "new_user",
  "duration": 7,
  "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
  "idempotency_key": "550e8400-e29b-41d4-a716-446655440000"
}
```
## Request Field
<table header-row="true">
<tr>
<td>필드</td>
<td>Type</td>
<td>필수</td>
<td>설명</td>
</tr>
<tr>
<td>`actor_id`</td>
<td>string</td>
<td>Y</td>
<td>Demo 사용자 ID. Audit 및 승인 검증용이며 Agent Prompt/Context에는 전달하지 않음</td>
</tr>
<tr>
<td>`event_name`</td>
<td>string</td>
<td>Y</td>
<td>이벤트명</td>
</tr>
<tr>
<td>`target_user`</td>
<td>string</td>
<td>Y</td>
<td>이벤트 대상</td>
</tr>
<tr>
<td>`duration`</td>
<td>integer</td>
<td>Y</td>
<td>이벤트 기간 1\~7일</td>
</tr>
<tr>
<td>`objective`</td>
<td>string</td>
<td>Y</td>
<td>이벤트 목적</td>
</tr>
<tr>
<td>`idempotency_key`</td>
<td>string</td>
<td>Y</td>
<td>중복 요청 방지 Key</td>
</tr>
</table>
### Input Validation 기준
- `actor_id`: 1\~50자
- `event_name`: 1\~100자
- `objective`: 1\~500자
- `duration`: 1\~7의 정수
- `idempotency_key`: UUID 문자열, 최대 64자
- `approval_comment`: `REQUEST_REVISION`일 때만 필수, 1\~500자
`APPROVE`와 `REJECT`에서는 `approval_comment`를 보내지 않습니다.
---
# 6. Test Mode 이벤트 생성
## Endpoint
```plain text
POST /api/test/events/generate
```
## Request
```plain text
{
  "actor_id": "liveops01",
  "event_name": "신규 유저 7일 성장 이벤트",
  "target_user": "new_user",
  "duration": 7,
  "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
  "fault_type": "mission_unreachable_once",
  "idempotency_key": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```
---
## fault_type
```plain text
mission_unreachable_once
agent_failure_once
primary_tool_error_once
mcp_timeout_once
db_connection_error_once
validation_error_once
retry_limit_exceeded
```
<table header-row="true">
<tr>
<td>값</td>
<td>설명</td>
</tr>
<tr>
<td>`mission_unreachable_once`</td>
<td>달성 불가능 미션 최초 1회 생성</td>
</tr>
<tr>
<td>`agent_failure_once`</td>
<td>Agent 최초 1회 실패</td>
</tr>
<tr>
<td>`primary_tool_error_once`</td>
<td>Primary Tool 최초 1회 실패</td>
</tr>
<tr>
<td>`mcp_timeout_once`</td>
<td>MCP 최초 1회 Timeout</td>
</tr>
<tr>
<td>`db_connection_error_once`</td>
<td>DB 최초 1회 연결 실패</td>
</tr>
<tr>
<td>`validation_error_once`</td>
<td>Output Validation 최초 1회 실패</td>
</tr>
<tr>
<td>`retry_limit_exceeded`</td>
<td>Retry 한도 초과</td>
</tr>
</table>
Test Mode 실행 여부는 Backend의 다음 설정으로 최종 제한합니다.
```plain text
ENABLE_FAULT_INJECTION=true
```
---
# 7. 이벤트 생성 Response
이벤트 생성 API는 동기식으로 실행합니다.
Backend는 유효한 요청에 대해 Workflow를 `WAITING_APPROVAL` 또는 `FAILED`까지 실행한 뒤 **Task Response**를 반환합니다.
`workflow_status=FAILED`는 HTTP/API 호출 실패와 구분합니다. Workflow가 Retry 또는 AutoHealing을 수행한 뒤 Safe Stop으로 정상 종료된 경우에도 Task가 생성되었으므로 정상 Task Response를 반환합니다.
## Response 예시 — WAITING_APPROVAL
```json
{
  "task_id": "TASK-001",
  "workflow_status": "WAITING_APPROVAL",
  "critic_status": "PASS",
  "approval_status": "PENDING",
  "result_version": 1,
  "retry_count": 0,
  "revision_count": 0,
  "owner_agent": null,
  "current_result": {
    "event_name": "신규 유저 7일 성장 이벤트",
    "target_user": "new_user",
    "duration": 7,
    "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
    "event_concept": "7일 동안 영웅 성장하기",
    "operation_summary": "매일 지정된 성장 미션을 완료하면 보상을 획득합니다.",
    "missions": [
      {
        "day": 1,
        "day_title": "모험의 시작",
        "content_id": "HUNT_001",
        "content_name": "일반 사냥",
        "mission_type": "monster_kill",
        "mission_name": "일반 사냥 20회",
        "target_count": 20,
        "planning_reason": "게임의 기본 전투 방식에 익숙해지도록 구성"
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
  },
  "rule_validation": {
    "is_valid": true,
    "error_type": null,
    "reason": "모든 게임 규칙 검증을 통과했습니다.",
    "evidence": {
      "expected_level": 15,
      "required_level": 10
    },
    "game_data_version": "tinyheroes-1.0.0",
    "data_source": "primary"
  },
  "autohealing_events": [],
  "final_result": null,
  "error": null
}
```
## Response 예시 — FAILED
```json
{
  "task_id": "TASK-004",
  "workflow_status": "FAILED",
  "critic_status": null,
  "approval_status": null,
  "result_version": 0,
  "retry_count": 3,
  "revision_count": 0,
  "owner_agent": null,
  "current_result": null,
  "rule_validation": null,
  "autohealing_events": [],
  "final_result": null,
  "error": {
    "error_type": "mcp_timeout",
    "message": "MCP 호출 재시도 한도를 초과하여 Workflow를 안전 종료했습니다."
  }
}
```
---
# 8. 이벤트 결과 current_result Contract
Frontend 이벤트 결과 화면에서 사용하는 Event Result Schema입니다.
**핵심 원칙**
- `missions[]`와 `rewards[]`는 분리합니다.
- Frontend는 `day` 기준으로 Mission과 Reward를 묶어 같은 Day 카드에 표시합니다.
- `current_result`와 승인 후 `final_result`는 동일한 Event Result Schema를 사용합니다.
- `objective`는 사용자가 입력한 원문을 유지합니다.
```json
{
  "event_name": "신규 유저 7일 성장 이벤트",
  "target_user": "new_user",
  "duration": 7,
  "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
  "event_concept": "7일 동안 영웅 성장하기",
  "operation_summary": "매일 지정된 성장 미션을 완료하면 보상을 획득합니다.",
  "missions": [
    {
      "day": 1,
      "day_title": "모험의 시작",
      "content_id": "HUNT_001",
      "content_name": "일반 사냥",
      "mission_type": "monster_kill",
      "mission_name": "일반 사냥 20회",
      "target_count": 20,
      "planning_reason": "게임의 기본 전투 방식에 익숙해지도록 구성"
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
## Event Result 상위 필드
<table header-row="true">
<tr>
<td>필드</td>
<td>Type</td>
<td>필수</td>
<td>설명</td>
</tr>
<tr>
<td>`event_name`</td>
<td>string</td>
<td>Y</td>
<td>이벤트명</td>
</tr>
<tr>
<td>`target_user`</td>
<td>string</td>
<td>Y</td>
<td>이벤트 대상</td>
</tr>
<tr>
<td>`duration`</td>
<td>integer</td>
<td>Y</td>
<td>이벤트 기간</td>
</tr>
<tr>
<td>`objective`</td>
<td>string</td>
<td>Y</td>
<td>사용자가 입력한 이벤트 목적 원문</td>
</tr>
<tr>
<td>`event_concept`</td>
<td>string/null</td>
<td>N</td>
<td>표시용 이벤트 컨셉</td>
</tr>
<tr>
<td>`operation_summary`</td>
<td>string/null</td>
<td>N</td>
<td>표시용 운영 방식 요약</td>
</tr>
<tr>
<td>`missions`</td>
<td>array</td>
<td>Y</td>
<td>Day별 미션 목록</td>
</tr>
<tr>
<td>`rewards`</td>
<td>array</td>
<td>Y</td>
<td>Day별 보상 목록</td>
</tr>
</table>
## Mission 필드
<table header-row="true">
<tr>
<td>필드</td>
<td>Type</td>
<td>필수</td>
<td>설명</td>
</tr>
<tr>
<td>`day`</td>
<td>integer</td>
<td>Y</td>
<td>이벤트 Day</td>
</tr>
<tr>
<td>`day_title`</td>
<td>string/null</td>
<td>N</td>
<td>Day 카드 표시용 제목</td>
</tr>
<tr>
<td>`content_id`</td>
<td>string/null</td>
<td>Y</td>
<td>콘텐츠 ID</td>
</tr>
<tr>
<td>`content_name`</td>
<td>string/null</td>
<td>Y</td>
<td>MCP 데이터 기준 화면 표시용 콘텐츠명</td>
</tr>
<tr>
<td>`mission_type`</td>
<td>string</td>
<td>Y</td>
<td>미션 Type</td>
</tr>
<tr>
<td>`mission_name`</td>
<td>string</td>
<td>Y</td>
<td>화면 표시용 미션명</td>
</tr>
<tr>
<td>`target_count`</td>
<td>integer</td>
<td>Y</td>
<td>수행 횟수</td>
</tr>
<tr>
<td>`planning_reason`</td>
<td>string/null</td>
<td>N</td>
<td>Day 미션의 표시용 기획 의도</td>
</tr>
</table>
## Reward 필드
<table header-row="true">
<tr>
<td>필드</td>
<td>Type</td>
<td>필수</td>
<td>설명</td>
</tr>
<tr>
<td>`day`</td>
<td>integer</td>
<td>Y</td>
<td>보상을 표시할 이벤트 Day</td>
</tr>
<tr>
<td>`item_id`</td>
<td>string</td>
<td>Y</td>
<td>보상 아이템 ID</td>
</tr>
<tr>
<td>`item_name`</td>
<td>string/null</td>
<td>Y</td>
<td>MCP 데이터 기준 화면 표시용 아이템명</td>
</tr>
<tr>
<td>`quantity`</td>
<td>integer</td>
<td>Y</td>
<td>보상 수량</td>
</tr>
</table>
한 Day에 여러 Mission 또는 Reward가 있으면 같은 `day` 값을 가진 항목을 모두 반복 표시합니다.
`event_concept`, `operation_summary`, `missions[].day_title`, `missions[].planning_reason`은 **Optional 표시 필드**입니다. 값이 `null`이면 Frontend는 해당 UI 영역을 숨기며, 이 값이 없다는 이유로 Workflow Validation을 실패시키지 않습니다.
Frontend에서 임의로 ID → 이름 변환을 하지 않도록 `content_name`, `mission_name`, `item_name`은 Backend가 제공합니다.
## 8-1. Rule Validation Contract
Frontend 결과 화면은 Backend A/B의 `RuleValidationResult`와 동일한 구조를 우선 사용합니다.
```json
{
  "rule_validation": {
    "is_valid": false,
    "error_type": "mission_unreachable",
    "reason": "신규 유저가 레이드에 접근할 수 없습니다.",
    "evidence": {
      "day": 7,
      "expected_level": 15,
      "required_level": 30,
      "content_id": "RAID_001"
    },
    "game_data_version": "tinyheroes-1.0.0",
    "data_source": "primary"
  }
}
```
Frontend가 MVP에서 사용하는 필드는 다음과 같습니다.
```plain text
is_valid
error_type
reason
evidence
game_data_version
data_source
```
`evidence`는 실패 원인을 설명하는 검증 근거이며 실패 시 필수로 제공합니다. `checks[]`는 이번 MVP 필수 계약에서 제외하고 후속 확장으로 둡니다.
---
# 9. AutoHealing Contract
Frontend에서는 AutoHealing 발생 시 다음 화면을 제공합니다.
```plain text
오류 발생
↓
AutoHealing
↓
복구 결과
```
한 Workflow에서 복구가 여러 번 발생할 수 있으므로 이벤트 생성 및 Task 조회 Response에는 단일 객체가 아니라 `autohealing_events` 배열을 포함합니다.
## AutoHealing 발생
```plain text
{
  "autohealing_events": [
    {
      "sequence": 1,
      "error_type": "mission_unreachable",
      "reason": "신규 유저 Day 7 예상 레벨은 Lv.15지만 레이드 입장 조건은 Lv.30입니다.",
      "recovery_action": "RUN_AGENT",
      "target": "event_planner_agent",
      "before": {
        "day": 7,
        "content_id": "RAID_001",
        "content_name": "레이드",
        "mission_name": "레이드 보스 1회 처치",
        "required_level": 30
      },
      "after": {
        "day": 7,
        "content_id": "DUNGEON_001",
        "content_name": "던전",
        "mission_name": "던전 3회 완료",
        "required_level": 10
      },
      "revalidation_status": "PASS"
    }
  ]
}
```
## AutoHealing 미발생
```plain text
{
  "autohealing_events": []
}
```
---
# 10. AutoHealing recovery_action
다음 값을 사용합니다.
```plain text
RUN_AGENT
RETRY
TOOL_FALLBACK
SAFE_STOP
```
<table header-row="true">
<tr>
<td>값</td>
<td>의미</td>
</tr>
<tr>
<td>`RUN_AGENT`</td>
<td>이벤트 기획 오류 수정 등을 위해 특정 Agent를 재실행. `target`에 Agent 이름 제공</td>
</tr>
<tr>
<td>`RETRY`</td>
<td>Agent / Tool / DB 작업을 동일 단계에서 재시도</td>
</tr>
<tr>
<td>`TOOL_FALLBACK`</td>
<td>Primary Tool 실패 후 Fallback Tool 사용</td>
</tr>
<tr>
<td>`SAFE_STOP`</td>
<td>자동복구 실패 후 안전 종료</td>
</tr>
</table>
---
# 11. Task 조회
## Endpoint
```plain text
GET /api/tasks/{task_id}
```
Frontend에서는 다음 상황에서 호출합니다.
```plain text
페이지 새로고침
Frontend 재접속
최신 Result Version 확인
승인 전 최신 상태 확인
```
현재 MVP에는 Task 목록 조회 API가 없으므로 Frontend가 보유한 현재 `task_id`를 기준으로 조회합니다.
## Response
```json
{
  "task_id": "TASK-001",
  "workflow_status": "WAITING_APPROVAL",
  "critic_status": "PASS",
  "approval_status": "PENDING",
  "result_version": 1,
  "retry_count": 0,
  "revision_count": 0,
  "owner_agent": null,
  "current_result": {
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
        "content_id": "HUNT_001",
        "content_name": "일반 사냥",
        "mission_type": "monster_kill",
        "mission_name": "일반 사냥 20회",
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
  },
  "rule_validation": {
    "is_valid": true,
    "error_type": null,
    "reason": "모든 게임 규칙 검증을 통과했습니다.",
    "evidence": {},
    "game_data_version": "tinyheroes-1.0.0",
    "data_source": "primary"
  },
  "autohealing_events": [],
  "final_result": null,
  "error": null
}
```
---
# 12. Human Approval
## Endpoint
```plain text
POST /api/tasks/{task_id}/approval
```
하나의 API에서 다음 Action을 처리합니다.
```plain text
APPROVE
REQUEST_REVISION
REJECT
```
---
# 13. APPROVE
## Request
```json
{
  "actor_id": "liveops01",
  "result_version": 1,
  "action": "APPROVE"
}
```
## Response
```json
{
  "task_id": "TASK-001",
  "workflow_status": "COMPLETED",
  "critic_status": "PASS",
  "approval_status": "APPROVED",
  "result_version": 1,
  "current_result": {
      "event_name": "신규 유저 7일 성장 이벤트",
      "target_user": "new_user",
      "duration": 7,
      "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
      "event_concept": null,
      "operation_summary": null,
      "missions": [],
      "rewards": []
    },
  "final_result": {
      "event_name": "신규 유저 7일 성장 이벤트",
      "target_user": "new_user",
      "duration": 7,
      "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
      "event_concept": null,
      "operation_summary": null,
      "missions": [],
      "rewards": []
    }
}
```
Backend 처리:
```plain text
final_result = current_result
approval_status = APPROVED
workflow_status = COMPLETED
```
`current_result`와 `final_result`는 모두 **# 8 Event Result Schema**를 사용합니다.
---
# 14. REQUEST_REVISION
## Request
```json
{
  "actor_id": "liveops01",
  "result_version": 1,
  "action": "REQUEST_REVISION",
  "approval_comment": "Day 1~3 미션을 전투보다 성장 중심으로 변경해주세요."
}
```
## 처리
```plain text
REQUEST_REVISION
↓
approval_status = REVISION_REQUESTED
↓
workflow_status = RUNNING
↓
critic_status = null
↓
approval_comment 저장
↓
Trace action = human_revision
↓
Event Planner 재실행
↓
전체 Validation
↓
Balance & Rule
↓
Critic PASS
↓
새 current_result 저장
↓
result_version + 1
↓
approval_status = PENDING
↓
workflow_status = WAITING_APPROVAL
```
## Response
```json
{
  "task_id": "TASK-001",
  "workflow_status": "WAITING_APPROVAL",
  "critic_status": "PASS",
  "approval_status": "PENDING",
  "result_version": 2,
  "retry_count": 0,
  "revision_count": 0,
  "current_result": {
      "event_name": "신규 유저 7일 성장 이벤트",
      "target_user": "new_user",
      "duration": 7,
      "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
      "event_concept": null,
      "operation_summary": null,
      "missions": [],
      "rewards": []
    },
  "rule_validation": {
    "is_valid": true,
    "error_type": null,
    "reason": "수정된 이벤트가 게임 규칙 검증을 통과했습니다.",
    "evidence": {},
    "game_data_version": "tinyheroes-1.0.0",
    "data_source": "primary"
  },
  "autohealing_events": [],
  "final_result": null
}
```
Human의 `REQUEST_REVISION`은 AutoHealing의 `revision_count`를 증가시키지 않습니다.
---
# 15. REJECT
## Request
```json
{
  "actor_id": "liveops01",
  "result_version": 1,
  "action": "REJECT"
}
```
## Response
```json
{
  "task_id": "TASK-001",
  "workflow_status": "CANCELLED",
  "critic_status": "PASS",
  "approval_status": "REJECTED",
  "result_version": 1,
  "current_result": {
      "event_name": "신규 유저 7일 성장 이벤트",
      "target_user": "new_user",
      "duration": 7,
      "objective": "신규 유저가 초반 콘텐츠에 적응하고 자연스럽게 성장할 수 있도록 한다.",
      "event_concept": null,
      "operation_summary": null,
      "missions": [],
      "rewards": []
    },
  "final_result": null
}
```
`CANCELLED`는 운영자 거절이며 시스템 복구 실패인 `FAILED`와 구분합니다.
---
# 16. Result Version 정책
`result_version`은 **유효한 current_result가 저장된 횟수**를 의미합니다.
```plain text
Workflow 시작 / 첫 Critic PASS 이전
result_version = 0

첫 Critic PASS 후 current_result 저장
result_version = 1

이후 새로운 current_result 저장
result_version + 1
```
따라서 첫 결과가 저장되기 전 AutoHealing 과정의 Trace는 `result_version=0`을 사용합니다.
Frontend는 승인 요청 시 현재 화면에서 사용자가 확인한 `result_version`을 Backend에 전달합니다.
Backend 최신 Version과 다르면 `409 Conflict`를 반환합니다.
```json
{
  "error_code": "STALE_RESULT_VERSION",
  "message": "새로운 이벤트 결과가 존재합니다. 최신 결과를 확인해주세요.",
  "task_id": "TASK-001",
  "detail": {
    "current_result_version": 3
  }
}
```
Frontend는 오류를 받은 뒤 다음 API로 최신 결과를 다시 읽습니다.
```plain text
GET /api/tasks/TASK-001
```
---
# 17. Trace 조회
## Endpoint
```plain text
GET /api/tasks/{task_id}/trace
```
별도 Tool Log API를 이번 MVP에서 사용하지 않으므로 **Agent / Supervisor / MCP Tool / DB 관련 실행 이력을 모두 Trace에서 확인할 수 있어야 합니다.**
## Response 예시
```json
{
  "task_id": "TASK-001",
  "traces": [
    {
      "sequence": 1,
      "time": "2026-10-07T14:23:19+09:00",
      "actor": "balance_rule_agent",
      "action": "validate_plan",
      "status": "ERROR",
      "error_type": "mission_unreachable",
      "reason": "required_level > expected_level",
      "retry_count": 0,
      "revision_count": 0,
      "result_version": 0
    },
    {
      "sequence": 2,
      "time": "2026-10-07T14:23:20+09:00",
      "actor": "supervisor",
      "action": "RUN_AGENT",
      "status": "SUCCESS",
      "error_type": "mission_unreachable",
      "reason": "Event Planner 재실행",
      "retry_count": 0,
      "revision_count": 1,
      "result_version": 0
    },
    {
      "sequence": 3,
      "time": "2026-10-07T14:23:25+09:00",
      "actor": "event_planner_agent",
      "action": "regenerate_plan",
      "status": "SUCCESS",
      "error_type": null,
      "reason": null,
      "retry_count": 0,
      "revision_count": 1,
      "result_version": 0
    },
    {
      "sequence": 4,
      "time": "2026-10-07T14:23:29+09:00",
      "actor": "mcp",
      "action": "get_game_rules",
      "status": "SUCCESS",
      "error_type": null,
      "reason": null,
      "data_source": "primary",
      "game_data_version": "tinyheroes-1.0.0",
      "retry_count": 0,
      "revision_count": 1,
      "result_version": 0
    },
    {
      "sequence": 5,
      "time": "2026-10-07T14:23:32+09:00",
      "actor": "critic_agent",
      "action": "final_review",
      "status": "SUCCESS",
      "error_type": null,
      "reason": "PASS",
      "retry_count": 0,
      "revision_count": 1,
      "result_version": 1
    }
  ]
}
```
---
# 18. Trace 필드
<table header-row="true">
<tr>
<td>필드</td>
<td>Type</td>
<td>설명</td>
</tr>
<tr>
<td>`time`</td>
<td>datetime</td>
<td>실행 시간</td>
</tr>
<tr>
<td>`actor`</td>
<td>string</td>
<td>실행 주체</td>
</tr>
<tr>
<td>`action`</td>
<td>string</td>
<td>실행 행동</td>
</tr>
<tr>
<td>`status`</td>
<td>string</td>
<td>실행 결과</td>
</tr>
<tr>
<td>`error_type`</td>
<td>string/null</td>
<td>오류 Type</td>
</tr>
<tr>
<td>`reason`</td>
<td>string/null</td>
<td>실행 / 오류 사유</td>
</tr>
<tr>
<td>`retry_count`</td>
<td>integer</td>
<td>기술 Retry</td>
</tr>
<tr>
<td>`revision_count`</td>
<td>integer</td>
<td>이벤트 자동 수정 횟수</td>
</tr>
<tr>
<td>`result_version`</td>
<td>integer</td>
<td>결과 Version</td>
</tr>
</table>
---
# 19. Trace actor 값
다음 값을 기준으로 사용합니다.
```plain text
input_guard
supervisor
event_planner_agent
balance_rule_agent
critic_agent
human
backend
mcp
database
```
---
# 20. Trace status 값
```plain text
RUNNING
SUCCESS
ERROR
RETRYING
WAITING
BLOCKED
FAILED
```
---
# 21. MVP 후속 API
다음 기능은 이번 MVP의 핵심 범위에서 제외하고 후속 확장으로 둡니다.
```plain text
GET /api/tasks
GET /api/tasks/{task_id}/tool-logs
GET /api/game-data
GET /api/system/health
```
### Tool Log
별도 `/tool-logs` API와 Tool Log 저장소는 MVP에서 구현하지 않습니다. Tool 호출, Retry, Fallback 여부는 `GET /api/tasks/{task_id}/trace`의 Trace 이벤트로 확인합니다.
### 게임 데이터 조회
별도 `/api/game-data`는 MVP에서 제공하지 않습니다. Frontend가 필요한 검증 근거와 표시명은 이벤트 결과의 `content_name`, `item_name`, `rule_validation.evidence`, `game_data_version`, `data_source`를 사용합니다.
### 종합 시스템 상태
별도 `/api/system/health`는 MVP에서 제공하지 않습니다. Backend 자체 상태는 `/health/live`와 `/health/ready`를 사용합니다.
---
# 26. Backend Health Check
## Live
```plain text
GET /health/live
```
정상:
```plain text
{
  "status": "ok"
}
```
---
## Ready
```plain text
GET /health/ready
```
정상:
```plain text
{
  "status": "ready"
}
```
서비스 의존성 문제 발생:
```plain text
503 Service Unavailable
```
---
# 27. 공통 Error Response
모든 API Error는 동일한 구조를 사용합니다.
```plain text
{
  "error_code": "MCP_TIMEOUT",
  "message": "MCP Server 응답 시간이 초과되었습니다.",
  "task_id": "TASK-001",
  "detail": null
}
```
## 필드
<table header-row="true">
<tr>
<td>필드</td>
<td>Type</td>
<td>설명</td>
</tr>
<tr>
<td>`error_code`</td>
<td>string</td>
<td>시스템 오류 Code</td>
</tr>
<tr>
<td>`message`</td>
<td>string</td>
<td>Frontend 표시 메시지</td>
</tr>
<tr>
<td>`task_id`</td>
<td>string/null</td>
<td>관련 Task</td>
</tr>
<tr>
<td>`detail`</td>
<td>object/array/string/null</td>
<td>추가 오류 정보. STALE_RESULT_VERSION의 최신 버전, 입력 필드 오류 목록 등에 사용</td>
</tr>
</table>
---
# 28. Error Code
```plain text
INPUT_VALIDATION_ERROR
INPUT_GUARD_BLOCKED
FAULT_INJECTION_DISABLED
INVALID_FAULT_TYPE

TASK_NOT_FOUND

INVALID_APPROVAL_STATUS
STALE_RESULT_VERSION
INVALID_ACTOR

AGENT_EXECUTION_ERROR
MCP_TIMEOUT
MCP_TOOL_ERROR
DB_CONNECTION_ERROR
VALIDATION_ERROR

MAX_RETRIES_EXCEEDED

INTERNAL_SERVER_ERROR
```
---
# 29. HTTP Status Code
<table header-row="true">
<tr>
<td>Status</td>
<td>사용</td>
</tr>
<tr>
<td>`200`</td>
<td>정상</td>
</tr>
<tr>
<td>`201`</td>
<td>Task 생성 성공</td>
</tr>
<tr>
<td>`400`</td>
<td>잘못된 요청</td>
</tr>
<tr>
<td>`403`</td>
<td>권한 / Test Mode 차단</td>
</tr>
<tr>
<td>`404`</td>
<td>Resource 없음</td>
</tr>
<tr>
<td>`409`</td>
<td>Version 또는 상태 충돌</td>
</tr>
<tr>
<td>`422`</td>
<td>Pydantic Schema Validation 실패</td>
</tr>
<tr>
<td>`500`</td>
<td>Backend 내부 오류</td>
</tr>
<tr>
<td>`502`</td>
<td>Task Response를 만들 수 없는 요청 수준의 MCP / OpenAI 등 의존 서비스 오류. Workflow가 FAILED Task로 정상 종료된 경우에는 사용하지 않음</td>
</tr>
<tr>
<td>`503`</td>
<td>서비스 준비 안 됨</td>
</tr>
</table>
---
# 30. 날짜 / 시간
모든 API의 시간은 ISO 8601 형식을 사용합니다.
```plain text
2026-10-07T14:23:19+09:00
```
Frontend에서 화면 표시 시 필요에 따라 다음과 같이 변환합니다.
```plain text
14:23:19
```
---
# 31. retry_count와 revision_count
두 값은 반드시 분리합니다.
## retry_count
기술 장애로 같은 작업을 다시 실행한 횟수입니다.
예:
```plain text
Agent Exception
MCP Timeout
DB Connection Error
```
Retry 성공 후:
```plain text
retry_count = 0
```
---
## revision_count
이벤트 기획 오류를 AutoHealing으로 수정한 횟수입니다.
예:
```plain text
mission_unreachable
item_not_found
event_brief_mismatch
```
Human의 `REQUEST_REVISION`은 `revision_count`를 증가시키지 않습니다.
---
# 32. Idempotency
## 이벤트 생성
Frontend에서 `idempotency_key`를 생성해 전달합니다.
```plain text
{
  "idempotency_key": "550e8400-e29b-41d4-a716-446655440000"
}
```
동일 Key 요청이 다시 들어오면 Backend는 새로운 Task나 중복 DB Write를 만들지 않습니다.
---
## Approval
Approval 관련 요청은 Backend에서 다음 값으로 Server Idempotency Key를 생성합니다.
```plain text
task_id
+
result_version
+
action
```
동일 승인 요청이 반복되어도 실제 상태 변경은 한 번만 수행합니다.
---
# 33. Frontend에서 반드시 필요한 Response 값
이벤트 생성 및 Task 조회 시 최소 다음 필드를 반환합니다.
```plain text
task_id

workflow_status
critic_status
approval_status

result_version

retry_count
revision_count

owner_agent

current_result
rule_validation
autohealing_events
final_result

error
```
---
# 34. Frontend 화면별 사용 API
## Page 1. 이벤트 생성
```plain text
POST /api/events/generate
POST /api/test/events/generate
```
게임 데이터는 별도 API로 조회하지 않고 화면의 참고 가이드는 고정 안내용으로 표시하거나 결과 Response의 검증 정보를 사용합니다.
---
## Page 2. 진행 상태 / 결과
```plain text
GET /api/tasks/{task_id}
GET /api/tasks/{task_id}/trace
POST /api/tasks/{task_id}/approval
```
표시 데이터:
```plain text
Workflow Status
Agent / Tool Trace 요약
AutoHealing
Event Result
Rule Validation Evidence
Human Approval
```
---
## Page 3. 운영 · 디버그
```plain text
GET /api/tasks/{task_id}/trace
GET /health/live
GET /health/ready
```
Page 3는 현재 Task의 상세 Trace와 Backend Health를 중심으로 구성합니다.
---
# 35. 보안
Frontend 및 API Response에는 다음 정보를 전달하지 않습니다.
```plain text
OPENAI_API_KEY

Database Password

MCP Secret

GitHub Secret

SSH Key

전체 System Prompt

전체 Agent Prompt

.env 내용
```
Error Response 및 Trace에서도 Secret을 반환하지 않습니다.
---
# 36. 최종 API 흐름
```plain text
사용자
↓
Frontend 이벤트 입력
↓
POST /api/events/generate
또는
POST /api/test/events/generate

↓
Backend Workflow

Event Planner
↓
Balance & Rule
↓
필요 시 AutoHealing
↓
Critic
↓
WAITING_APPROVAL

↓
Frontend

Workflow 결과 표시
AutoHealing 표시
Event Result 표시

↓
운영 담당자

├ APPROVE
├ REQUEST_REVISION
└ REJECT

↓
POST /api/tasks/{task_id}/approval

↓
Backend

상태 검증
result_version 검증
actor_id 검증

↓
최종 상태 반환

↓
Frontend 화면 갱신
```
---
# 37. API 구현 완료 기준
- [ ] `POST /api/events/generate` 구현
- [ ] `POST /api/test/events/generate` 구현
- [ ] `GET /api/tasks/{task_id}` 구현
- [ ] `POST /api/tasks/{task_id}/approval` 구현
- [ ] `GET /api/tasks/{task_id}/trace` 구현
- [ ] `/health/live` 구현
- [ ] `/health/ready` 구현
- [ ] Event Result를 `missions[] / rewards[]` 분리 구조로 적용
- [ ] `current_result`와 `final_result` 동일 Schema 적용
- [ ] Optional 표시 필드 null 허용
- [ ] Rule Validation `evidence` Contract 적용
- [ ] AutoHealing Events Contract 적용
- [ ] Agent / Tool 실행을 포함한 Trace Contract 적용
- [ ] 공통 Error Response 적용
- [ ] `STALE_RESULT_VERSION.detail.current_result_version` 적용
- [ ] Workflow FAILED와 HTTP/API 실패 구분
- [ ] workflow_status / critic_status / approval_status Enum 적용
- [ ] fault_type Enum 적용
- [ ] result_version 0 → 1 저장 시점 적용
- [ ] UUID 기반 Idempotency 적용
- [ ] ISO 8601 시간 형식 적용
- [ ] Secret 정보 Response 제외
### 후속 확장
- [ ] `GET /api/tasks`
- [ ] `GET /api/tasks/{task_id}/tool-logs`
- [ ] `GET /api/game-data`
- [ ] `GET /api/system/health`
---
# 38. 추가 확정 규칙
## 38-1. Trace 확장 필드
Trace는 Agent뿐 아니라 Supervisor, MCP Tool, DB 관련 Retry/Fallback 실행도 기록할 수 있습니다.
```json
{
  "sequence": 4,
  "time": "2026-10-07T14:23:20+09:00",
  "actor": "supervisor",
  "actor_id": null,
  "action": "TOOL_FALLBACK",
  "status": "SUCCESS",
  "error_type": "mcp_tool_error",
  "reason_code": "PRIMARY_TOOL_FAILED",
  "reason": "Primary Tool 실패로 Fallback Tool을 실행했습니다.",
  "policy_decision": "ALLOW",
  "failed_tool": "get_game_rules",
  "fallback_tool": "get_game_rules_fallback",
  "data_source": "fallback_snapshot",
  "game_data_version": "tinyheroes-1.0.0",
  "retry_count": 0,
  "revision_count": 0,
  "result_version": 0
}
```
사용하지 않는 상세 필드는 `null`을 허용합니다.
---
## 38-2. 빈 값 표현 규칙
```plain text
Object 데이터 없음 → null
List 데이터 없음   → []
Optional 표시 문자열 없음 → null
```
`event_concept`, `operation_summary`, `day_title`, `planning_reason`이 `null`이면 Frontend는 해당 영역을 숨깁니다.
---
## 38-3. Idempotency 충돌 규칙
이벤트 생성의 `idempotency_key`는 Frontend에서 UUID로 생성합니다.
```plain text
동일 idempotency_key + 동일 Request Body
→ 기존 Task 결과 반환

동일 idempotency_key + 다른 Request Body
→ 409 Conflict
```
```json
{
  "error_code": "IDEMPOTENCY_KEY_CONFLICT",
  "message": "동일한 idempotency_key가 다른 요청에 이미 사용되었습니다.",
  "task_id": "TASK-001",
  "detail": null
}
```
HTTP Timeout 또는 네트워크 오류 후 Frontend가 재요청할 때도 **같은 idempotency_key를 재사용**합니다.
---
## 38-4. Pydantic 422 공통 Error Contract
FastAPI 기본 422 Response를 그대로 사용하지 않고 공통 Error Response 구조로 변환합니다.
```json
{
  "error_code": "INPUT_VALIDATION_ERROR",
  "message": "입력값을 확인해주세요.",
  "task_id": null,
  "detail": [
    {
      "field": "duration",
      "reason": "1 이상 7 이하의 값이어야 합니다."
    }
  ]
}
```
---
## 38-5. Frontend API Timeout
<table header-row="true">
<tr>
<td>API 종류</td>
<td>Timeout</td>
</tr>
<tr>
<td>이벤트 생성</td>
<td>180초</td>
</tr>
<tr>
<td>Test Mode 이벤트 생성</td>
<td>180초</td>
</tr>
<tr>
<td>REQUEST_REVISION</td>
<td>180초</td>
</tr>
<tr>
<td>일반 GET API</td>
<td>10초</td>
</tr>
<tr>
<td>APPROVE / REJECT</td>
<td>30초</td>
</tr>
</table>
Frontend HTTP Timeout은 Backend 내부 MCP Tool Timeout / Retry 정책과 별개입니다.
---
## 38-6. Workflow FAILED와 HTTP 오류
유효한 요청의 Workflow가 `WAITING_APPROVAL` 또는 `FAILED`까지 실행되었다면 Task Response를 반환합니다.
`workflow_status=FAILED`는 HTTP 5xx로 치환하지 않습니다. Frontend가 Task의 실패 원인과 Trace를 화면에서 확인할 수 있어야 합니다.
HTTP/API 오류는 Task 결과를 반환할 수 없는 경우에 사용합니다.
예:
```plain text
Backend 연결 실패
네트워크 오류
HTTP Client Timeout
잘못된 Request
Pydantic Validation 오류
Backend 자체 처리 오류
```
---
## 38-7. 요청 사용자 actor_id 원칙
`actor_id`는 Audit / Trace / Demo 승인 사용자 검증 / 요청 생성자 식별에만 사용하며 LLM Agent Prompt 또는 Agent Context에는 전달하지 않습니다.
---
## 38-8. game_data_version / Mock ID 기준
Frontend 예시와 계약 테스트는 Backend/MCP 공통 Fixture와 동일한 값을 사용합니다.
현재 백엔드/MCP 기준 예:
```plain text
game_data_version = tinyheroes-1.0.0

HUNT_001
DUNGEON_001
RAID_001

ITEM_001
ITEM_002
ITEM_003
```
브랜드명과 기술 데이터 버전명은 별개이며, 데이터 버전을 변경할 경우 Frontend / Backend / MCP / Test Fixture를 동시에 변경합니다.
---
# 39. 최종 Response 원칙
이벤트 생성과 Task 조회 Response는 최소 다음 구조를 공통으로 유지합니다.
아래 `{}`는 **Schema 위치를 설명하기 위한 축약 표기**이며 실제 Response에서는 해당 데이터가 존재하면 정의된 전체 객체를 반환합니다.
```json
{
  "task_id": "TASK-001",
  "workflow_status": "WAITING_APPROVAL",
  "critic_status": "PASS",
  "approval_status": "PENDING",
  "result_version": 1,
  "retry_count": 0,
  "revision_count": 0,
  "owner_agent": null,
  "current_result": {},
  "rule_validation": {},
  "autohealing_events": [],
  "final_result": null,
  "error": null
}
```
- `current_result`와 `final_result`는 동일한 Event Result Schema를 사용합니다.
- Event Result는 `missions[]`와 `rewards[]`를 분리합니다.
- 데이터가 없는 객체는 `null`, 목록은 `[]`를 사용합니다.
- `event_concept`, `operation_summary`, `missions[].day_title`, `missions[].planning_reason`은 Optional이며 `null`을 허용합니다.
- `objective`는 사용자 입력 원문을 유지합니다.
- Rule Validation은 MVP에서 `evidence`를 사용하며 `checks[]`는 후속 확장입니다.
---
# 40. 화면 시안 정합성 및 API 확장 검토 (미구현)
이 절은 첨부된 이벤트 생성·결과·운영 화면 시안과 현재 MVP 계약의 차이를 기록합니다. **1\~39절의 MVP 계약은 그대로 유효**하며, 아래 확장 항목은 Backend와 Frontend가 필드·검증·저장 방식을 합의하고 구현하기 전까지 사용 가능한 API로 간주하지 않습니다.
## 40-1. 현재 계약으로 표시할 수 있는 정보
- 이벤트 생성 요청은 `actor_id`, `event_name`, `target_user`, `duration`(1\~7), `objective`, `idempotency_key`만 전송합니다. Test Mode는 별도 경로와 `fault_type`을 사용합니다.
- Day별 Mission·Reward, Rule Validation의 `is_valid`·`reason`·`evidence`, AutoHealing 전후 결과, Critic·Approval 상태, 상세 Trace는 현행 Task/Trace Response로 표시합니다.
- `event_concept`, `operation_summary`, `day_title`, `planning_reason`은 생성 **결과의 Optional 표시 필드**입니다. 생성 요청 입력 필드가 아니며 `null`이면 숨깁니다.
- MCP·Backend·Database 실행 기록은 Trace의 `actor`와 `action`으로 필터링할 수 있습니다. 원시 시스템 로그나 별도 Tool Log API가 제공된다는 뜻은 아닙니다.
## 40-2. 시안과 동일한 동적 화면을 위한 추가 계약 후보
- 추가 설정 입력: 이벤트 콘셉트, 운영 방식, 콘텐츠·보상 선호, 추가 요구사항을 실제 생성에 반영하려면 생성 및 Test Mode 요청 스키마, 입력 검증, 저장 구조, Agent 입력 규칙을 함께 확정해야 합니다. 필드명·허용 값·최대 길이는 확정 전이므로 Frontend가 임의 필드를 전송하지 않습니다.
- 최근 이벤트: 고정된 ‘예시 불러오기’는 Frontend에서 제공할 수 있습니다. 실제 최근 Task 목록은 조회 API와 접근 범위·정렬·페이지 크기 계약이 추가되어야 합니다. 현행 `duration`은 최대 7일이므로 시안의 14일 예시는 그대로 생성 요청에 사용할 수 없습니다.
- 게임 데이터 현황: 콘텐츠·아이템·레벨·규칙 건수와 MCP별 연결 상태는 현행 API에 없습니다. 실제 수치를 표시하려면 데이터 요약 응답의 출처, 버전, 갱신 시각, 장애 시 응답을 별도로 정의해야 합니다. `/health/ready`만으로 개별 MCP 상태나 건수를 추정하지 않습니다.
- 항목별 검증 체크리스트: 시안의 개별 PASS 항목에는 Backend가 실제 수행한 검사별 결과 목록이 필요합니다. 현재 `rule_validation.evidence`를 임의의 `checks[]`로 변환하지 않습니다.
- 실행 요약: 정확한 시작·종료 시각과 총 소요 시간은 Task/Trace에 명시적인 실행 요약 필드가 필요합니다. Trace 첫·마지막 시각의 차이는 근사치일 뿐 전체 실행 시간으로 단정하지 않습니다.
- 입력 데이터 탭: 새로고침·FAILED Task에서도 원 요청을 표시하려면 비밀값을 제외한 저장된 요청 입력을 Task 조회 응답에 추가해야 합니다. 동일 세션의 입력값만 보여줄 경우 그 한계를 UI에 표시합니다.
## 40-3. 구현 순서와 파일 책임
1. Backend는 먼저 현행 7개 MVP 엔드포인트와 1\~39절 응답 계약을 구현·검증합니다.
2. 추가 화면 요소의 데이터·권한·오류 계약을 확정한 뒤 확장 API를 별도 변경으로 구현합니다.
3. `backend/app/api/event.py`는 생성, Test Mode 생성, Task 조회, 승인, Trace 라우트를 담당하고 `backend/app/api/health.py`는 Live·Ready 라우트를 담당합니다. 라우트가 비어 있는 현재 저장소 상태를 완료로 표시하지 않습니다.
4. Frontend는 실제 응답이 없는 항목에 가짜 연결 상태, 건수, 검증 PASS, 실행 시간을 표시하지 않습니다.

