# 백엔드 A/B 연동 결정 및 B 전달 사항

기준: `backend-test`의 MCP 서버·B 전송 코드·A Adapter·Frontend 화면 및 `docs/frontend_backend_api_contract.md` (2026-10-08). B 가이드 9장의 미확정 항목은 아래 현재 계약과 대조한다.

| 항목 | 결정 | 담당 |
| --- | --- | --- |
| 실제 전송 선택 | `WORKFLOW_ENGINE=backend_b_mock`은 Mock LLM/MCP, `backend_b_mcp`는 Mock LLM+실제 MCP, `backend_b_live`는 실제 OpenAI+MCP | A가 Adapter와 실행 설정 관리; B는 전송 함수 계약 유지 |
| MCP 주소·SDK | `MCP_URL`은 Streamable HTTP의 `/mcp` 전체 주소. 백엔드와 MCP 서버 모두 `mcp>=1.27,<2` | A가 의존성·환경변수·준비 상태 반영 |
| OpenAI SDK | `openai>=2,<3`. 실제 호출 모드에서 `OPENAI_MODEL`·`OPENAI_API_KEY`를 필수로 지정하고 키는 저장소에 넣지 않음 | A가 실행 모드·의존성 관리; B가 Responses 전송 구현 유지 |
| 준비 상태 | 기본 모드는 DB 검사, 실제 MCP 모드는 DB와 `get_game_rules(HUNT_001)`의 실제 조회·형식 확인. OpenAI 네트워크 호출은 하지 않음 | A |
| Mission·Reward | API 결과에서 별도 배열을 유지하고 같은 `day`로 화면에 묶음. 한 Day의 여러 Mission·Reward를 허용하며 개별 연결이나 보상 복제를 하지 않음 | B가 Day 범위 검증, A가 API 형식 유지 |
| `rule_validation.checks[]` | 현재 MVP의 필수 필드가 아님. 없으면 `[]`; Frontend는 `reason`, `error_type`, `evidence`, 출처·버전을 표시 | B가 실제 검사 근거 생성; A가 검증 결과 그대로 전달 |
| 결과·AutoHealing·Trace | B 내부 결과·이벤트는 A Adapter의 Pydantic API 형식과 Trace sequence·저장 버전으로 변환 | 필드나 의미를 바꾸는 쪽이 사전에 상대 담당자와 계약·테스트를 갱신 |

근거: `frontend/components/event_result.py`는 Day별 두 목록을 별도로 렌더링한다. `docs/frontend_backend_api_contract.md`는 `checks[]`를 후속 확장으로 둔다. `backend/app/services/mcp_client.py`에는 MCP SDK 1.x Streamable HTTP 전송이 이미 있다. MCP 서버의 기본 주소는 `mcp_server/README.md`에 명시되어 있다.

확인 결과: MCP 서버에서 API까지 `backend_b_mcp`로 실행해 정상 생성 시 `WAITING_APPROVAL`·`primary`와 Primary 오류 주입 시 `fallback_snapshot`을 확인했다. `/health/ready`도 실제 `HUNT_001` 조회로 통과했다. 실제 OpenAI 호출, 전체 Docker Compose, 운영 환경 Timeout은 아직 검증하지 않았다.

## 백엔드 B 담당자에게 전달할 작업

1. B 내부 Planner·Critic 결과와 AutoHealing 이벤트, Trace 필드가 위 Adapter 계약과 달라지면 변경 필드·예시 응답·관련 테스트를 A에게 전달한다.
2. 실제 MCP 서버 데이터에서는 신규 유저 Day 3 예상 레벨이 8이고 `DUNGEON_001` 필요 레벨이 10이다. 실제 MCP를 쓰는 3일 기획안은 이 조합을 그대로 반환하면 검증 실패하므로 Planner의 수정 경로 또는 Fixture를 확인한다. A는 해당 결과를 임의로 PASS로 바꾸지 않는다.
3. `checks[]`를 MVP 필수로 가정하지 않는다. 후속 확장에서 화면별 PASS 목록을 요청하면 B가 실제 `RuleCheck`를 외부로 전달하는 계약을 A와 추가한다.
4. 실제 OpenAI 경로는 모델·키·비용 정책을 팀에서 정하고 별도 시험한다. 현재 `backend_b_live`는 설정과 전송 함수 연결까지이며 실제 모델 응답 품질은 검증 전이다.

## 프론트엔드 Trace 추가 요청

API 응답 키는 현재 화면이 사용하는 `sequence`, `time`, `actor`, `action`, `status`, `reason`, `error_type`을 유지한다. A는 선택 필드 `phase`(`planning_context`·`validation`·`revalidation`)와 `tool_name`을 추가하고 B Trace Sink의 값을 보존한다. `game_data_version`·`data_source`는 확인된 값만 저장한다. 목표 실행 순서와 정상·기획 오류·첫 MCP 조회 실패의 축약 JSON은 `docs/team/backend-1/trace_contract_examples.md`에 있다. 기획 전 조회·검증용 조회·재검증의 실제 Trace 발생은 B Engine과 새 MCP Tool 연동 후 검증한다.

## 기획용 조회 Loader 연결점

A는 `backend/app/services/planning_context_loader.py`에 비동기 `load(target_user: str, duration: int) -> dict[str, object]`를 만들고 `BackendBWorkflowAdapter.planning_context_loader`에 바인딩했다. 내부 호출은 고정 Tool 이름 `get_event_planning_context`와 정수형 `duration`을 사용한다. 현재 최소 응답 Envelope은 요청과 일치하는 `target_user`·`duration`, 비어 있지 않은 `game_data_version`, `source=primary|fallback_snapshot`이다. 상세 콘텐츠·아이템·일자별 성장 기준은 MCP 담당자의 확정 응답 계약을 기다린다. 이 최소 Envelope 역시 MCP 담당자와 최종 대조해야 한다.

연결 순서는 다음과 같다: MCP 담당자가 Tool·응답 계약을 구현 → B가 `ToolTransport`의 인자를 `dict[str, object]`로 확장하고 Tool Guard에 새 Tool을 등록 → B Engine 생성자에 `planning_context_loader` 콜백을 추가하고 Planner 전에 호출 → A가 Adapter 생성자에서 `planning_context_loader=self.planning_context_loader`를 전달한다. B는 재시도·Fallback·Trace·상세 응답 검증을 소유한다. 현재 B 생성자에는 해당 인자가 없어 A는 아직 실제 Engine 호출 경로에 주입하지 않았다. 따라서 새 Loader 자체는 단위 시험만 통과했고 새 MCP Tool의 실제 전송은 미검증이다.
