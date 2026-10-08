# 기획 전 게임 데이터 조회 Tool 개발 요청

## 상태와 목적

2026-10-08 `port` 병합 후 새 `get_event_planning_context` Tool이 MCP 서버에 등록됐다. 기존 세 Tool은 Planner가 선택한 ID의 기획 후 검증에 사용한다.

## 합의된 새 Tool 계약

- Tool 이름·입력: `get_event_planning_context(target_user, duration)`.
- 응답 필드: 요청과 일치하는 `target_user`·정수형 `duration`, 현재 게임 데이터처럼 ID를 키로 하는 `contents`·`items`, 요청 대상 기준을 담은 `user_segments`, 실제 조회 데이터의 `game_data_version`, 정상 조회 시 `source=primary`.
- 신규·복귀 유저의 `expected_levels`는 기간 안의 1·3·5·7일 기준만 담고, 중간 날은 가장 최근 기준을 적용한다. `all_user`는 `content_access_only`와 공통 접근 레벨을 사용한다.
- Backend는 Tool 결과의 형식과 버전을 검증한 뒤 기획에 필요한 정보만 Event Planner Context에 전달한다. Planner에는 MCP 직접 호출 권한을 주지 않는다.
- 기존 ID별 Tool은 Planner가 선택한 미션·보상을 기획 후 재조회·검증하는 용도로 유지한다.
- 기획용 대체 Tool은 이번 범위에서 제공하지 않는다. 입력·DB 조회 실패는 Tool 오류로 전달한다. Backend가 유효한 데이터를 끝내 얻지 못하면 추측으로 초안을 만들지 않고 Safe Stop한다.
- 기획 전 결과와 기획 후 ID별 결과의 버전이 다르면 Backend는 서로 다른 버전을 섞어 PASS 처리하지 않는다.

이 계약은 MCP 담당자 전달 내용과 병합된 Tool 코드를 반영한다. JSON 기본 데이터를 사용하는 실제 HTTP 호출에서 신규·복귀·전체 유저 응답이 A Loader와 Backend B 검증을 통과했다. 공용 PostgreSQL 데이터 적용과 DB 통합 시험은 아직 진행 전이다.

Backend B는 이 ID별 응답을 검증해 Planner 전용 `daily_expected_levels`, 접근 가능한 `available_contents`(`content_id`, `required_level`), `reward_item_ids`, `game_data_version`, `source`로 변환한다. `all_user`에는 `daily_expected_levels` 대신 `common_access_max_required_level`을 전달한다. 이 내부 형태는 MCP 서버의 응답 형식이 아니다. MCP 응답의 `source`는 Backend Trace의 `data_source`에 기록한다.
