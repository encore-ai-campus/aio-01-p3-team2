# Backend A 팀 사용 PostgreSQL 구축·적용 계획서

> **기준선 안내 (2026-10-13, `ikonly`):** 이 문서는 2026-10-09 `checkpoint` 기준의 계획·검증 기록이다. `checkpoint`는 당시 브랜치 기준을 가리킬 뿐 현재 작업·통합 대상이 아니다. 배포 표의 `infra/compose.yml`, `infra/compose.release.yml` 상태도 당시 기록으로 읽는다. 현행은 로컬 PostgreSQL용 `infra/compose.yml`과 빈 과거 초안 `infra/compose.release.yml`이며, 현재 배포·CI/CD 계약과 결과는 IK의 [현재 구현 상태](../../ik/현재_구현상태.md)와 [CI/CD 검증 기록](../../ik/검증산출물/2026-10-13_현행코드_CICD_마무리.md)을 따른다. 당시 팀 DB 적용 전 확인 사항은 이력으로 보존한다.

작성일: 2026-10-09
대상 기준: `checkpoint` 브랜치
상태: **Backend A 순번 보강·전용 PostgreSQL 검증 완료, 팀 사용 DB 적용 대기**. 팀이 함께 사용할 DB의 생성·변경·백업은 아직 실행하지 않았다.

## 합의된 적용 기준

- Backend A(임다혁)가 **새 빈 PostgreSQL DB 하나**를 구축한다. 기존 운영 DB의 데이터 이전이나 기존 테이블 변경은 범위 밖이다.
- Backend는 우선 **단일 프로세스**로 운영한다. 다만 한 프로세스에서도 동시 요청은 발생할 수 있으므로 Trace·Tool log 순번을 DB에서 보호한 뒤 적용한다.
- 새 DB를 처음 만들 때 Trace·Tool log의 `(task_id, sequence)` 고유 제약과 트랜잭션 내 원자적 순번 할당을 함께 도입한다. 둘 중 하나만 적용하지 않는다.
- 외래 키 도입은 이번 범위에서 보류한다. 삭제 흐름과 멱등성 기록의 수명 정책을 정할 때 다시 검토한다.
- Backend A가 DB 생성과 애플리케이션 스키마·초기 데이터 적용을 맡는다. 계정 권한 부여 방식과 필요한 운영 권한은 팀장과 확정한다.
- 백업·복구 책임자를 지정하고 별도 DB에서 복구를 검증한 뒤에만 팀 사용 DB를 적용한다.

## 1. 목적과 범위

이 문서의 **팀 사용 DB**는 Backend A가 구축하는 PostgreSQL **한 개의 데이터베이스**를 뜻한다. Backend A의 Task·Trace·Tool log·멱등성 기록과 게임 데이터 카탈로그를 여기에 저장한다. Backend는 `DATABASE_URL`로 운영 기록을 쓰고, MCP는 **바로 그 DB**의 `game_data_catalog`를 `MCP_DATABASE_URL`로 읽는다. MCP 담당자는 별도 DB를 만들거나 같은 초기 데이터를 중복 적용하지 않는다. Backend B는 이 두 계약을 이용하는 Workflow를 실행하며 DB 스키마를 직접 관리하지 않는다.

Clash of Clans 공개 API 표본은 MCP가 외부에서 조회한다. `game_data_catalog`의 `tiny_heroes` 초기 데이터와 별개이며, 실제 Clash 계정의 미션·보상을 저장하거나 적용하는 DB를 만드는 계획이 아니다.

## 2. 현재 구현과 미결정 사항

| 구분 | 현재 확인된 상태 | 적용 전에 결정할 사항 |
| --- | --- | --- |
| Backend 운영 기록 | `workflow_runs`, `task_creation_order`, `traces`, `idempotency_records`, `tool_logs`를 SQLAlchemy `create_all`로 생성 | 이번 신규 DB 초기 생성에만 사용. 이후 스키마 변경용 마이그레이션·롤백 절차는 별도 결정 |
| 게임 데이터 | `backend.scripts.apply_game_data_catalog`가 `game_data_catalog` 테이블과 `tiny_heroes` 초기값을 적용. 기존 행의 버전·내용이 다르면 중단 | 적용할 데이터 버전과 변경 승인 방식 |
| 연결 | `DATABASE_URL`이 없으면 Backend는 인메모리. MCP는 `MCP_DATABASE_URL` 설정 시 PostgreSQL 조회 | DB 호스트·DB명·계정·접속 방식·비밀값 보관 위치 |
| 검증 | 전용 로컬 PostgreSQL에서 저장·재시작·동시성, 별도 임시 DB에서 실제 MCP·OpenAI·Clash 연동 확인 | Backend A가 구축한 팀 사용 DB에서 동일 버전으로 재검증할 일정과 담당자 |
| 배포 (2026-10-09 기록 당시) | 당시 기준에서 `infra/compose.yml`, `infra/compose.release.yml`이 비어 있다고 확인 | 당시 Compose/배포 구성을 누가 작성하고 승인할지 논의가 남아 있었음. 현행 상태와 배포 절차는 상단 기준선 안내를 따른다. |
| 순번 무결성 | `checkpoint` 기준 코드에는 고유 제약과 원자적 할당이 없었음 | Backend A 작업 브랜치에서 보강·전용 PostgreSQL 동시성 검증 완료. 팀 사용 DB 확인은 별도 |

### 순번 코드 확인 결과

기준 `checkpoint`의 `task_service._trace()`와 `workflow_service._save_trace()`는 각각 기존 Trace 목록을 읽고 `len(traces) + 1`을 새 순번으로 부여했다. `SqlAlchemyRepository.append_trace()`는 별도 트랜잭션으로 행을 추가했고 `(task_id, sequence)` 고유 제약이 없었다. 따라서 **단일 프로세스에서도 같은 Task에 대한 두 요청이 동시에 읽으면 중복 순번이 가능했다**. `append_tool_log()`도 호출자가 지정한 순번을 별도 트랜잭션으로 저장했다. 현재 Backend 앱에서 Tool log를 실제로 추가하는 호출 경로는 확인되지 않았지만, 향후 기록을 시작할 때 같은 검증이 필요하다.

이번 신규 DB 적용 전 아래 방식으로 Backend A 코드와 테스트를 보강했다. 고유 제약만 추가하고 기존의 `len + 1` 계산을 유지하면 충돌이 조용한 중복에서 요청 오류로 바뀔 뿐이므로 함께 변경했다.

1. Backend A가 `TraceRow`와 `ToolLogRow`에 각각 `(task_id, sequence)` 고유 제약을 정의한다. 새 빈 DB에서 생성하므로 기존 중복 행 정리나 데이터 이전은 필요하지 않다.
2. Repository의 append 작업은 **같은 DB 트랜잭션 안에서** 해당 Task의 `workflow_runs` 행을 `SELECT ... FOR UPDATE`로 잠근 뒤, 해당 기록의 최대 순번에 1을 더해 삽입한다. Trace와 Tool log는 각자의 순번 공간을 사용한다. 전달받은 `TraceEntry`/`ToolLogEntry`의 순번과 JSON payload도 실제 할당 순번으로 일치시킨다. Task가 없으면 기록하지 않는다.
3. 서비스 계층의 `len(traces) + 1`을 순번의 최종 결정값으로 사용하지 않도록 정리한다. PostgreSQL 이외 저장 모드의 인터페이스·기존 API 응답 계약은 유지하면서 별도 동기화 방식을 확인한다.
4. 전용 PostgreSQL에서 동일 Task에 대한 동시 Trace·Tool log append를 반복 시험한다. 건수·순번의 유일성·연속성·payload 순번 일치와 조회 순서를 확인한다. 일반 Workflow와 재시작 후 조회 테스트도 다시 실행한다.

2026-10-09 전용 PostgreSQL에서 세 Repository 인스턴스·8개 작업 스레드로 같은 Task에 Trace와 Tool log를 각각 40건 기록했다. 두 테이블의 고유 제약 존재, 순번 1~40의 유일성·연속성, 모든 기록의 보존을 확인했다. 전체 백엔드 테스트는 `104 passed, 11 skipped`였다. 제외된 시험은 별도 키·환경이 필요한 항목이다. 이는 로컬 코드와 전용 DB 결과이며 팀 사용 DB에 적용했다는 뜻은 아니다. Backend B에는 Trace 저장 인터페이스나 조회 결과에 영향이 있는지 확인받는다.

### 데이터가 이미 있는 DB의 추가 검증

팀장 의견에 따라 **별도 임시 PostgreSQL**에 테스트용 Task·Trace·Tool log와 `tiny_heroes` 카탈로그를 먼저 저장한 다음 같은 DB로 Repository를 다시 생성했다. 기존 Task·카탈로그는 유지되고 새 Trace·Tool log는 각각 2번 순번으로 추가됐다. 관련 PostgreSQL 테스트는 `12 passed`였다.

같은 DB에 실제 MCP HTTP 서버와 Backend API(`backend_b_mcp`, Mock LLM)를 연결했다. 사전 입력 Task는 API에서 조회 가능했고, 새 이벤트는 HTTP 201·`WAITING_APPROVAL`·Critic `PASS`로 생성됐다. 새 Task의 Trace 15건은 순번 1~15이며 MCP 조회의 `data_source=primary`, `game_data_version=tinyheroes-1.0.0`을 확인했다. Backend를 종료·재시작한 뒤 사전 입력 Task(Trace 1건)와 새 Task(Trace 15건)가 모두 보존됐다. 임시 서버와 DB는 테스트 후 종료·삭제했다.

이 검증은 **이번 코드로 만든 최신 스키마에 데이터가 들어 있는 경우**다. 과거 스키마에 `(task_id, sequence)` 고유 제약이 없는 DB를 자동으로 갱신한다는 뜻은 아니다. SQLAlchemy `create_all`은 기존 테이블에 그 제약을 추가하지 않는다. 따라서 팀 사용 DB 적용 전 새 빈 DB 여부와 실제 제약을 확인하고, 비어 있지 않은 과거 스키마가 발견되면 자동 적용을 중단해 별도 마이그레이션을 결정한다.

## 3. 역할과 승인

| 담당 | 제안 역할 |
| --- | --- |
| 팀장·통합 담당 | Backend A가 구축할 팀 사용 DB의 적용 시점·중단 가능 시간 승인. 계정 권한 방식과 백업·복구 책임자 확정. 최종 통합 결과 승인 |
| Backend A (임다혁) | 팀 사용 DB 생성, 운영 기록 스키마와 `game_data_catalog` 초기 데이터 적용, Backend 연결·Task·Trace 보존 확인, 변경 기록 정리 |
| MCP 담당 | Backend A가 만든 **같은 DB**의 카탈로그 조회 계약과 `source`·`game_data_version` 검증. 별도 DB 생성·초기 데이터 중복 적용은 하지 않음 |
| Backend B 담당 | 정상·복구·안전 종료 Workflow와 Trace를 Backend A가 구축한 팀 사용 DB 환경에서 검증. DB 스키마 변경은 하지 않음 |
| Frontend 담당 | 생성·Trace·승인 화면의 최종 연결 확인 |

Backend A가 팀 사용 DB의 **생성·애플리케이션 스키마·초기 데이터 적용**을 맡는다. 호스트 운영·백업 보관·복구 실행 책임은 팀장과 별도 확정한다.

## 4. 적용 단계와 통과 조건

### 0단계 — 팀 승인 및 입력값 확정

Backend A가 **새 빈 DB**를 구축한다. 팀장은 DB 호스트·접속 허용 범위·적용 시점·백업 보관 책임을 확정한다. Backend A와 MCP 담당자는 동일한 DB를 가리키되 가능한 한 별도 계정을 사용한다. Backend는 운영 기록 쓰기와 카탈로그 적용 권한이 필요하고, MCP는 카탈로그 조회에 필요한 최소 권한을 받는다. 계정 생성·권한 부여 방식은 팀장과 확정하고 사전 검증 DB에서 확인한다. 실제 URL·비밀번호·API 키는 Git·문서·채팅에 기록하지 않는다.

지정된 DB가 이미 존재한다면 새 테이블을 바로 만들지 않는다. 테이블 목록, 현재 스키마와 데이터 버전, 다른 서비스의 사용 여부를 읽기 전용으로 확인하고 차이를 기록한다. 예상 밖의 테이블·데이터 충돌이 있으면 적용을 중단한다.

### 1단계 — 동일 조건의 검증 DB 리허설

팀 사용 DB와 분리된 검증 PostgreSQL에서 승인된 코드 버전으로 운영 테이블 생성과 카탈로그 적용을 먼저 반복한다. Backend A는 `DATABASE_URL`을 검증 DB로 지정해 Repository를 초기화하고 `python -m backend.scripts.apply_game_data_catalog`를 실행한다. 카탈로그 적용 명령은 같은 초기 데이터로 재실행해도 성공해야 하고, 서로 다른 기존 데이터는 덮어쓰지 않고 중단해야 한다.

검증 DB에서만 파괴적 자동화 테스트를 실행한다. `backend/tests/test_postgres_integration.py` 등은 `reset()`을 호출하므로 **팀 사용 DB에 `POSTGRES_TEST_URL`을 설정하지 않는다**. Backend와 MCP를 같은 검증 DB에 연결해 기획 전 조회의 버전·출처, 생성·승인·Trace, 프로세스 재시작 후 동일 Task 조회를 확인한다. 같은 Task에 대한 동시 Trace·Tool log 기록 시험에서 순번 중복 0건, 누락 0건, payload 불일치 0건을 통과해야 2단계로 진행한다.

### 2단계 — 백업과 복구 리허설

팀 사용 DB 적용 **전에** 지정된 책임자가 검증 DB를 백업하고 별도 복구 검증 DB에 실제 복원한다. 저장 위치·생성 시각·대상 DB·책임자를 기록하고, 복원된 테이블·카탈로그 버전·샘플 Task 조회를 확인한다. `pg_dump`/`pg_restore` 사용 방식은 팀 운영 환경에 맞춰 확정한다. **백업 파일이 존재한다는 확인만으로 완료하지 않는다.** 새 팀 사용 DB의 초기 적용 직후에도 기준 백업을 만든다. 복구 시험에서 팀 사용 DB를 덮어쓰지 않는다.

### 3단계 — 팀 사용 DB 적용

팀장 승인 시간에 Backend A가 승인된 스키마·카탈로그만 적용한다. 새 빈 DB 확인 → 애플리케이션 운영 테이블 초기화 → 카탈로그 적용 → 결과 기록 → 기준 백업 순서로 진행한다. 현재는 운영 테이블의 **신규 생성**에 `create_all`을 사용할 수 있지만, 이후 기존 테이블의 컬럼 변경은 별도 검토된 마이그레이션 없이는 진행하지 않는다. 카탈로그의 기존 행이 초기값과 다르면 스크립트가 중단하도록 그대로 둔다.

### 4단계 — 연결·보존·복구 흐름 검증

Backend의 `DATABASE_URL`과 MCP의 `MCP_DATABASE_URL`이 동일 DB인지 비밀값을 출력하지 않는 방식으로 확인한다. `/health/ready`만으로 저장·외부 API 전체를 보증하지 않으므로, 새 이벤트 생성 → MCP 기획 데이터 조회 및 버전·출처 Trace 확인 → Planner·규칙 검증·Critic → 승인 대기 → Backend 재시작 → 같은 Task·Trace 조회를 수행한다. Backend B와 함께 장애 재시도·안전 종료 사례도 확인한다. Clash 공개 API는 키 허용 IP·호출 한도 영향을 받으므로 실패 원인과 재시도 결과를 별도 기록한다.

### 5단계 — 인수인계

적용 코드 커밋, DB 대상 식별자(비밀값 제외), 적용·백업·복구 시각, 카탈로그 버전, 검증 Task ID와 Trace 결과, 미완료 사항을 팀장에게 전달한다. 팀장이 최종 시연 환경을 승인한 뒤에만 “팀 사용 DB 적용 완료”로 표시한다.

## 5. 실패 시 중단·복구 기준

- 승인되지 않은 DB 대상, 백업 부재, 예상 밖의 기존 스키마·카탈로그 버전, MCP와 Backend의 조회 버전 불일치가 확인되면 즉시 적용을 중단한다.
- 변경 전 데이터 복원이 필요하면 지정된 복구 책임자가 백업과 복구 검증 결과를 기준으로 실행한다. Backend A가 임의로 팀 사용 DB를 초기화하거나 `reset()`하지 않는다.
- 외부 Clash API 또는 OpenAI 장애는 DB 손상과 구분한다. 해당 호출의 실패 Trace와 Task 상태를 확인하고, DB 복원 여부를 외부 API 실패만으로 결정하지 않는다.
- 적용 결과가 불명확하면 기존 서비스를 계속 기동·재시도하기보다 쓰기를 멈추고 팀장·MCP·Backend B와 상태를 확인한다.

## 6. 팀장님께 요청할 결정

1. Backend A가 구축할 팀 사용 DB의 호스트·DB명, 계정 권한 방식과 적용 목표 일시는?
2. Backend A의 스키마·초기 데이터 적용 실행을 누가 승인할지? Backend/MCP 계정별 최소 권한은 무엇인지?
3. 백업 생성·보관·복구 실행 책임자와 별도 복구 검증 DB 준비 담당자는?
4. 최종 검증·시연의 코드 버전과 Backend B·MCP·Frontend 공동 확인 시간은?

이 결정 전까지 Backend A는 검증 DB에서 절차를 준비할 수 있지만 팀 사용 DB에는 적용하지 않는다.
