# PostgreSQL 데이터베이스 역할 분담 및 적용 경계

2026-10-09 팀 합의 기준. 이 문서는 Backend A·Backend B·MCP 담당자가 **하나의 PostgreSQL 데이터베이스**를 사용할 때의 개발 책임을 정리한다. 한 사람이 DB 전체 기능과 운영을 단독으로 맡는다는 뜻은 아니다. 로컬 테스트 DB에서의 검증과 팀 공용 DB 적용은 별개의 단계다.

| 담당 | 맡는 작업 | 다른 담당과 연결되는 지점 |
| --- | --- | --- |
| Backend A | Task·Trace·Tool log·멱등성 등 백엔드 운영 기록의 저장소와 스키마, `game_data_catalog` 테이블 및 초기 데이터 **적용 절차**, 백엔드의 `DATABASE_URL` 연결·보존 검증 | MCP가 읽을 게임 데이터 구조·버전을 확인하고, 공용 DB 적용 전에 변경 사항을 공유한다. |
| MCP | 같은 DB의 `game_data_catalog`를 읽는 Tool, 기획 전 조회·규칙 조회 응답의 필드·`source`·`game_data_version` 계약 | 필요한 게임 데이터 필드를 A와 맞춘다. 운영 기록 테이블을 관리하거나 공용 DB에 게임 데이터를 별도로 초기화하지 않는다. |
| Backend B | MCP 조회 결과를 기획·검증 Workflow에 연결하고, 오류·재시도·Fallback·안전 종료 및 실행 Trace 이벤트를 생성 | A의 Trace 저장·API 계약과 MCP의 조회 응답 계약을 사용한다. DB 스키마를 직접 변경하지 않는다. |

## 현재 구현과 로컬 검증

- Backend A는 `backend/app/repositories/postgres.py`에서 운영 기록을 PostgreSQL에 저장한다. `DATABASE_URL`이 비어 있으면 인메모리 저장소를 사용하며 재시작 후 데이터가 남지 않는다.
- `backend/scripts/apply_game_data_catalog.py`가 `backend/sql/001_game_data_catalog.sql`과 초기 JSON 데이터를 적용한다. 기존 `tiny_heroes` 행이 다른 버전·내용이면 덮어쓰지 않고 중단한다.
- MCP 서버는 `MCP_DATABASE_URL`을 설정하면 같은 DB의 게임 데이터를 읽는다. Backend와 MCP의 URL은 **같은 DB**를 가리켜야 하며, 각 서비스의 비밀값은 각자 로컬 환경에서 관리한다.
- Backend A는 별도 로컬 PostgreSQL에서 동시 요청 처리와, 새 프로세스로 재시작한 뒤 Task·Trace·Clash 표본 스냅샷을 다시 조회하는 테스트를 통과했다. 2026-10-09에는 초기 데이터의 반복 적용, 변경된 데이터의 덮어쓰기 거부, MCP DB 조회 코드가 같은 행을 읽는지까지 확인했다. `backend/tests/test_game_data_catalog.py`와 `backend/tests/test_postgres_integration.py`의 PostgreSQL 대상 시험은 합계 9건 통과했다. Clash 표본은 시험용 고정 데이터이며, 이 결과가 팀 공용 DB 적용이나 실제 MCP HTTP·OpenAI·Clash API 연동 완료를 뜻하지는 않는다.

## 공용 DB 적용 전에 함께 정할 사항

1. 적용할 DB와 시점, 적용 전 검증 DB 및 승인 담당자를 지정한다. Backend A가 스키마·초기 데이터 적용 절차를 제시하고, 팀장과 MCP 담당자가 연결·조회 영향을 확인한다.
2. 스키마 변경 방식을 정한다. 현재 운영 테이블은 SQLAlchemy `create_all`로 생성되지만 기존 테이블의 단계적 변경을 관리하는 마이그레이션 체계는 확정되지 않았다.
3. 백업 생성·보관·복구 담당자와 복구 확인 절차를 정한다. 이 문서는 백업이 이미 마련됐거나 특정 담당자가 운영 책임을 인수했다는 뜻이 아니다.
4. 적용 전·후에 Backend의 Task·Trace 보존과 MCP의 게임 데이터 버전·출처 조회를 각각 확인한다. 장애 테스트의 `reset()` 대상에는 공용 DB를 사용하지 않는다.

상세 실행 방법은 [Backend A 실행 문서](team/backend-1/runbook.md), 조회 Tool 계약은 [MCP 서버 설명](../mcp_server/README.md)을 따른다. 실제 키·비밀번호·DB URL은 이 문서나 Git에 기록하지 않는다.
