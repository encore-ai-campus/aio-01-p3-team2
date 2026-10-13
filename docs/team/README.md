# 팀 문서 색인

팀별 작업 자료와 저장소 공통 기록의 위치를 안내한다. 구현 현황의 기준은 [루트 README](../../README.md)에서 연결하는 제품 문서이며, 이 색인은 문서의 위치와 우선순위를 설명한다.

## 팀별 자료

| 영역 | 위치 | 범위 |
|---|---|---|
| Frontend | [frontend/](frontend/) | 화면 기획, 구현 기록, 연동 자료 |
| Backend 1 | [backend-1/](backend-1/) | 백엔드 작업 기록 및 검증 자료 |
| Backend 2 | [backend-2/](backend-2/) | 백엔드 작업 기록 및 검증 자료 |
| MCP | [mcp/](mcp/) | MCP 서버 작업 및 연동 자료 |

팀별 문서는 작업 당시의 계획·구현·인계 기록이다. 과거 Frontend 계획 문서에 적힌 `infra/compose.release.yml` 배포 흐름은 실행 가능한 현재 구성으로 간주하지 않는다. 현행 배포 기준은 IK 문서와 코드 영역 분류를 따른다.

## 저장소 루트의 공통 기록

`docs/` 루트의 `architecture.md`, `database_roles.md`, `frontend_backend_api_contract.md`, `workflow_evaluation_contract.md`, `fault_lab_execution_path.md`는 초기 협업 과정의 공통 설계·계약 기록이다. 당시 결정과 맥락을 보존하며 현재 구현을 단독으로 설명하는 기준 문서로 사용하지 않는다.

현재 제품 구현과 검증 상태는 [IK 문서 색인](../ik/README.md)을 우선 확인한다. 실행·배포와 오류·시험 자료는 [deployment.md](../deployment.md), [fault_scenarios.md](../fault_scenarios.md), [test_scenarios.md](../test_scenarios.md)의 안내를 따른다. 파일명과 과거 기록은 유지하고, 내용이 충돌하면 최신 구현 상태 및 날짜가 명시된 검증 원본을 기준으로 판단한다.

저장소 문서의 상태 분류·링크 점검·갱신 원칙은 [문서 관리대장](../문서_관리대장.md)을 따른다. 코드의 현행·보조·미사용 경로 분류는 [코드 영역 분류](../코드영역_분류.md)를 참조한다.
