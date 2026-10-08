# Backend A 구현 기록

## 역할

Backend A는 FastAPI 요청·응답, 공통 Schema, 상태 저장, 승인, 멱등성, Trace 조회와
Frontend 연동을 담당한다. Agent·MCP·AutoHealing 실행은 Backend B의 Workflow Engine 책임이다.

## 현재 구현: 1단계 MockEngine MVP

- `POST /api/events/generate`
- `POST /api/test/events/generate` (`ENABLE_FAULT_INJECTION=true` 필요)
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/trace`
- `POST /api/tasks/{task_id}/approval`
- `GET /health/live`
- `GET /health/ready`
- 공통 422 오류 응답
- 생성 요청 멱등성 및 충돌 검사
- APPROVE / REQUEST_REVISION / REJECT 상태 전이
- Frontend가 현재 읽는 `missions[]` / `rewards[]` 응답
- 인메모리 Repository와 Mock Workflow Engine

이 단계의 결과는 실제 AI·MCP 검증 결과가 아니다. `rule_validation.data_source`는
`mock_engine`이며 화면에도 실제 검증 전 단계라는 문구를 반환한다.

## 실행

프로젝트 루트에서:

```powershell
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --reload
```

테스트:

```powershell
python -m pytest backend/tests
ruff check backend
ruff format --check backend
```

## 2단계 영속화 기반

- `WorkflowRepository` Protocol 추가
- SQLAlchemy 기반 PostgreSQL Repository 추가
- SQLite 메모리 DB를 사용한 Repository 단위 테스트 추가
- `DATABASE_URL` 미설정 시 인메모리, 설정 시 SQLAlchemy Repository 선택
- 생성과 승인 Idempotency Key namespace 분리
- 동일 승인 요청 재전송 시 기존 결과 반환 및 Trace 중복 방지
- DB Ready Check 반영
- Backend Dockerfile과 환경변수 예시 작성

PostgreSQL을 실제 실행한 통합 시험은 Compose의 DB 서비스와 Secret 규칙이 팀에서 확정된 후 진행한다.

## 다음 단계

1. Backend B와 `WorkflowResult`, `run`, `revise`, Repository 계약 확정
2. PostgreSQL 컨테이너 통합 시험 및 마이그레이션 도구 확정
3. 승인 동시성 잠금과 진행 중 Idempotency 정책 확정
4. 실제 Backend B Engine 교체
5. MCP 기반 Ready Check
6. 조건부 추가 API 범위 확정

## 3단계 조회 계약 확장

- `GET /api/tasks?limit=20` 구현
- `GET /api/tasks/{task_id}/tool-logs` 구현
- 인메모리 및 SQLAlchemy Repository에 Task 목록과 Tool Log 저장 계약 추가
- Backend B가 Tool Log를 기록하기 전에는 `tool_logs: []` 반환
- `APPROVED_ACTOR_IDS` 환경변수로 Demo 승인 가능 사용자 목록 관리

다른 담당자의 디렉터리는 수정하지 않았다. Tool Log 실제 생성은 Backend B가 Repository
인터페이스의 `append_tool_log()`를 호출하도록 연동된 이후 검증한다.

## 4단계 Guard·회귀 시험·마무리 문서

- Input Guard 차단을 `INPUT_GUARD_BLOCKED` 공통 오류로 변환
- Task Response 반환 전 Response Guard 적용
- Task 없음, Test Mode 비활성, Health API 회귀 테스트 추가
- 실제 PostgreSQL 선택 실행 테스트 추가
- `runbook.md`에 실행·환경변수·Docker·B 연결 지점·미완료 항목 기록

## 검증 결과

- Backend 단위·API 테스트: 16 passed, PostgreSQL 선택 테스트 1건은 기본 실행에서 skip
- 실제 PostgreSQL 선택 테스트: 1 passed
- Ruff lint 및 format 검사: 통과
- Backend Docker 이미지 빌드: 통과
- PostgreSQL + Backend 임시 컨테이너 통합:
  - API로 Task 생성
  - Backend 컨테이너 제거 후 재실행
  - 동일 Task가 `WAITING_APPROVAL` 상태로 조회됨
- 검증용 임시 Backend·PostgreSQL 컨테이너와 Docker network는 제거함

## 미구현/제한

- `DATABASE_URL` 미설정 시 재시작 후 상태 보존 불가
- 실제 PostgreSQL 컨테이너 통합 시험 및 마이그레이션은 아직 미완료
- 실제 LLM, MCP, Rule Engine, AutoHealing 미연결
- 승인 동시 요청에 대한 DB Row Lock은 아직 미구현
- Tool Log, Task 목록, 게임 데이터, 종합 Health API 미구현
- Test API는 접근 제한만 적용하며 MockEngine이 fault를 실제 주입하지 않음

## 2026-10-09 저장된 Task 삭제

`DELETE /api/tasks/{task_id}`는 `{"actor_id":"liveops01"}` 요청을 받는다. Backend는 `APPROVED_ACTOR_IDS`에 등록된 Demo 담당자인지 검사하고, 실행 중(`RUNNING`·`RETRYING`)인 Task는 409로 거부한다. 존재하지 않는 Task는 404를 반환한다. 성공 시 `{"task_id":"...","deleted":true}`를 반환한다.

삭제는 해당 Task 본문, Trace, Tool Log, 생성 순서, 연결된 멱등성 기록을 하나의 저장소 작업으로 제거한다. PostgreSQL 저장소는 같은 DB 트랜잭션에서 삭제한다. 삭제 후에는 목록·상세·Trace·Tool Log 조회에서 해당 Task를 찾을 수 없다. 삭제된 승인 Task ID로 데모 게임을 시작할 수도 없다. 삭제는 복구할 수 없으며 실제 사용자 인증 대신 기존 Demo 담당자 ID 검사를 사용하므로 Backend를 외부에 직접 공개하지 않는다.
