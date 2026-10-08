# 기획 전 게임 데이터 조회 흐름

## 상태

2026-10-08 추가 개발 흐름이다. 현재 `feature/demo-game`에는 Backend A Adapter의 기획용 Loader 주입이 반영되어, `backend_b_mcp` API가 MCP 기획용 조회를 Planner보다 먼저 실행한다. 실제 MCP HTTP 서버와 JSON 기본 데이터·인메모리 Repository에서 정상·복구 경로의 API Trace를 확인했다. 최신 네 Agent 실제 LLM 시험과 실제 OpenAI·MCP·격리된 임시 PostgreSQL을 함께 사용하는 정상 API 요청 1건도 통과했다. 공용 PostgreSQL과 실제 LLM의 반복 품질 평가는 별도 검증 대상이다. 이전 개발 단계의 기록은 아래 목표·실패 처리 설명과 `docs/team/backend-2/handoff.md`에 남긴다.

## 목표 실행 순서

1. Backend가 기존 네 항목(`event_name`, `target_user`, `duration`, `objective`)의 이벤트 요청을 받는다.
2. Backend가 MCP의 기획용 조회 Tool `get_event_planning_context(target_user, duration)`을 호출한다. Event Planner에게 MCP Tool 직접 호출 권한을 주지 않는다.
3. Backend가 응답 형식과 `game_data_version`을 검증한다. 검증된 대상 유저의 일자별 예상 레벨, 사용 가능한 콘텐츠 ID·입장 레벨, 보상 아이템 ID 중 기획에 필요한 정보만 Planner Context에 전달한다.

4. Event Planner가 검증된 데이터로 미션·보상 초안을 만든다.
5. Balance & Rule이 초안에서 선택한 ID를 기존 `get_game_rules(content_id)`·`get_item_info(item_id)`로 다시 조회하고 Python Rule Engine으로 판정한다.
6. Critic이 원래 요청의 목적과 검증된 기획안을 검토한다. PASS한 후보만 운영자 승인 대기로 보낸다.

MCP 응답 키는 `target_user`, `duration`, ID별 객체 `contents`·`items`, 요청 대상 기준 `user_segments`, `game_data_version`, `source`다. 신규·복귀 유저는 기간 안의 1·3·5·7일 기준값을 쓰고 나머지 날은 가장 최근 기준값을 적용한다. `all_user`는 `content_access_only`의 공통 접근 기준을 사용한다. B는 실제 Tool 응답을 검증해 Planner용 자료로 변환한다. 공용 PostgreSQL 적용·통합 시험은 아직 완료되지 않았다.

## 실패와 데이터 일관성

- 기획 전 MCP 조회 실패는 Supervisor의 기술 재시도 대상이다. 이번 MCP 계약에는 기획용 대체 Tool이 없으므로 유효한 데이터를 끝내 확보하지 못하면 추측으로 초안을 만들지 않고 Safe Stop한다. 기존 `get_game_rules_fallback`의 검증용 사용은 유지한다.
- 기획안의 규칙 위반은 기존 기획 수정 경로로 Planner를 다시 호출하고, 수정안 전체를 재검증한다.
- 기획 전 조회와 기획 후 ID 검증 조회의 `game_data_version`이 다르면 서로 다른 버전의 결과를 섞어 PASS 처리하지 않는다. 일관된 버전으로 재조회·재검증하거나 Safe Stop한다.
- Trace에서 기획 전 조회와 기획 후 검증 조회를 구분한다. Tool·단계·출처·데이터 버전·오류·Retry/Fallback 결과를 기록하고, 화면 표시 방법은 Trace 계약 확정 후 결정한다.

## 담당과 계약 경계

- MCP 팀: 기획용 Tool과 응답 계약, 필요 시 검증된 Fallback 데이터 제공 방식.
- Backend B: 조회 → 응답 검증 → 제한된 Planner Context 전달 → 생성 → 기존 Tool 재조회·규칙 검증 순서, Supervisor 복구 및 Trace 이벤트 생성.
- Backend A: API·공통 Schema·Repository·Trace 저장과 실행 설정의 연결. 외부 이벤트 생성 API의 네 입력 항목은 유지한다.
- Frontend: Trace 계약이 확정된 뒤 실제 실행 순서에 맞춰 화면·데모를 조정한다.

정확한 Tool 응답 필드·타입, Fallback 제공 방식, 버전 변경 시 재조회 범위와 Trace 구분 필드는 팀 간 계약으로 확정한다. 이 문서는 미확정 세부 규격을 구현 완료로 간주하지 않는다.
