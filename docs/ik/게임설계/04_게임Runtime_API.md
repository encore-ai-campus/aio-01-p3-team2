# 게임 Runtime·API 설계서

## 문서 상태와 책임

- 상태: 논리 API 요청·응답·상태·오류 계약 정리 완료; 구현 계획에서 `/api/game/*`·`backend/app/api/game.py` 선택, 세부 OpenAPI/auth mapping 전
- 목적: 승인된 이벤트와 가상 플레이를 서버 권위로 처리하는 API/상태 계약을 제안한다.
- 상위 기준: [가상 게임 도메인 상세 설계서](../가상게임_도메인_상세설계서.md) §§5.2–5.4, 6
- 의존: [게임 규칙](01_게임규칙_플레이루프.md), [스키마](02_게임데이터_스키마.md), [저장소·DB](03_게임저장소_DB.md), [이벤트 적용 계약](05_이벤트적용_계약.md)
- 논리 요청·응답 및 도메인 상태 계약은 이 문서를 따른다. 물리 경로는 [가상 게임 구현 계획서](../가상게임_구현계획서.md)에 따라 `/api/game/*` 아래로 구현하며, 각 메서드·Pydantic field·오류 envelope는 구현 전에 이 문서에 확정한다.

## 권위와 경계

프론트엔드는 사용자 입력과 연출을 담당한다. Runtime은 유저 상태, 콘텐츠 입장, 성공/실패, 경험치, 행동 기록, 미션 진행, 보상 자격을 판정한다. 요청 바디로 전달한 `result=success`, 레벨, 보상 수량은 신뢰하지 않는다. 시스템 간 이벤트 적용은 승인 식별자와 결과 버전으로 검증한다.

## API 표면 초안

| 작업 | 경로 초안 | 성공 결과 |
|---|---|---|
| 게임 상태 조회 | `GET /api/game/state` | 고정 데모 유저 성장·해금·자원 상태와 기준 버전 |
| 콘텐츠 목록 | `GET /api/game/catalog/{content_type}` | 활성 콘텐츠와 입장 조건 |
| 이벤트 적용 | `POST /api/game/events/apply` | 적용 이벤트 ID·상태·설정 버전·영수증 |
| 이벤트 조회 | `GET /api/game/events/{event_id}` | 기간·상태·venue·진행 요약 |
| 플레이 행동 | `POST /api/game/actions` | 서버 판정, 행동 ID, 갱신 상태 |
| 이벤트 진행 조회 | `GET /api/game/events/{event_id}/progress` | 고정 데모 유저의 미션별 진행·완료·보상 상태 |
| 명령 결과 조회 | `GET /api/game/commands/{request_id}` | 최초 요청의 처리 상태와 저장된 결과 |

위 경로는 구현 계획이 선택한 기준 경로이며 코드 구현 완료를 뜻하지 않는다. 로컬 MVP는 단일 고정 데모 유저를 사용하며 user_id를 행동 요청에서 받지 않는다. 인증 principal이 없는 현재 Backend에서 actor/access를 어떻게 제한할지와 세부 request/response fields는 구현 전 local-only 보안 계약으로 확정한다.

논리 HTTP 결과는 다음과 같이 통일한다: 조회 성공과 도전 판정 완료(게임상 성공/실패 모두)는 `200`; 비동기 적용 또는 아직 처리 중인 명령은 `202`; 잘못된 구조/타입은 `400`; 존재하지 않거나 고정 스냅샷에서 비활성인 리소스는 `404`; 자격·기간·잠금·시도 한도 등 도메인 사전조건 거절은 `422`; 같은 멱등 키의 다른 payload·source digest 충돌·상태 버전 충돌은 `409`; 일시 의존 시스템 장애는 `503`으로 표현한다. 실제 API prefix와 공통 error envelope만 기존 Backend 규약에 맞춰 매핑한다.

## 요청·응답 논리 모델

행동 명령과 응답의 논리 계약은 아래와 같다. 이는 필드 의미를 확정하며 물리 OpenAPI/JSON Schema 문법은 구현 계획에서 변환한다.

```json
{
  "request_id": "<unique id>",
  "command_type": "stage_challenge | dungeon_challenge | special_dungeon_challenge",
  "content_id": "<catalog id>",
  "difficulty_id": "<required for ordinary dungeon>",
  "runtime_event_id": "<optional event association>"
}
```

유저 ID는 인증 주체 또는 MVP의 서버 측 고정 데모 유저 매핑으로 결정하고 본문은 받지 않는다. 서버 판정 응답은 아래 의미를 갖는다.

```json
{
  "request_id": "<same id>",
  "command_status": "processing | completed | rejected",
  "attempt": {"attempt_id": "<id>", "result": "succeeded | failed", "reason_code": "<code>"},
  "action": {"action_id": "<id>", "action_type": "<successful fact type>"},
  "state_changes": {"level_before": 3, "level_after": 4, "inventory_changes": []},
  "mission_updates": [],
  "reward_records": [],
  "ruleset_version": "<version>",
  "data_snapshot_id": "<snapshot>"
}
```

`command_status`는 명령 처리 상태이며 도전 판정 결과와 구분한다. `completed` 응답의 Attempt `result`가 `succeeded` 또는 `failed`일 수 있다. 도전 실패는 정상 게임 결과로 Attempt/시도권만 확정한다. 해금·기간·자격·시도권 불충족은 `rejected`이며 유효 도전 시도를 만들지 않는다. 요청이 진행 중이면 `processing` 상태와 같은 요청 ID로 조회할 수 있는 결과 참조를 반환한다. 서비스 오류는 별도 오류 envelope에 안정된 `code`, 사용자 안전 설명, 재시도 가능 여부, 상관관계 식별자를 제공한다. 비밀·내부 stack trace는 응답하지 않는다.

이벤트 진행 조회는 이벤트 ID·유저별 참여 상태, 이벤트 버전, 각 미션의 `current_value`, `target`, `status`, 근거 Action ID 목록, `completed_at`, 연결된 RewardRecord와 현재 가상 인벤토리 반영 상태를 반환한다. 읽기 응답은 권위 저장 상태에서 구성한다.

`user_id`는 본문 값만으로 인증하지 않는다. 인증 주체에서 식별자를 결정하고 본문에 값이 오더라도 일치 여부를 검사한다. 같은 사용자와 명령 범위에서 같은 `request_id`를 다시 보내면 최초 명령 결과를 반환한다. 이미 같은 키가 다른 payload hash로 처리되었으면 충돌을 반환하고 새 명령으로 실행하지 않는다. 클라이언트는 결과가 불명확한 네트워크 오류 뒤에 새 키를 만들지 않고 원 요청 ID로 결과를 조회/재전송한다.

서버는 다음 순서로 처리한다: 인증/입력 검증 → 멱등 키와 payload hash 확인 → 처리 중/완료된 명령 확인 → 단일 규칙·데이터 버전 고정 → 상태 및 참여 자격 읽기 → 입장·한도 판정 → 결정적 게임 결과 계산 → 행동/성장/미션/보상과 멱등 결과 원자 적용 → 결과 반환. 같은 유저 상태에 대한 동시 행동은 DB 잠금 또는 낙관적 버전 검사로 직렬화/충돌 처리해야 한다. 충돌로 거절된 요청은 판정을 다시 해야 한다면 동일 멱등 키의 결과를 덮어쓰지 않고 API 정책에 맞는 명시 응답을 반환한다. 저장소 경계에 따라 일부 작업이 분산되면 outbox/재조정 방식 등 최종 기술을 선택해야 한다.

행동 결과 시간은 서버가 부여한다. 이벤트 기간은 UTC의 `[start_at, end_at)`로 비교하고, 일일 시도권은 규칙 버전의 명시적 시간대·초기화 경계로 계산한다. MVP 권위 판정은 난수 없이 결정적으로 수행한다. 난수를 추가하는 변경은 규칙 버전 변경으로 취급하고 seed/알고리즘 재현 정보를 행동 기록에 남기기 전에는 허용하지 않는다.

명령 처리 상태의 논리 모델은 `received → processing → completed | rejected | retryable_failure`다. `completed`는 콘텐츠 도전 판정(성공 또는 게임상 실패)이 확정됐음을 뜻한다. `rejected`는 입장 불가·기간 종료 등 도메인 사전조건 거절로 재시도하지 않는다. `retryable_failure`는 원자 커밋 전 일시 장애처럼 안전 재시도가 확인된 경우에만 사용한다. 커밋 결과가 불명확하면 `reconciling`을 거쳐 같은 키의 상태를 조회해 중복 행동/보상을 막는다. 외부 이벤트 적용 상태는 05 문서의 별도 계약을 따른다.

## 오류 의미

| 경우 | 의미 | 클라이언트 동작 원칙 |
|---|---|---|
| 잘못된 입력/미지원 행동 (`invalid_request`, `unsupported_command`) | 계약 위반 | 수정 후 새 request ID |
| 없는/비활성 콘텐츠 (`content_not_found`, `content_inactive`) | 고정 스냅샷에서 참조 불가 | 카탈로그를 확인한 뒤 새 의도로 요청 |
| 해금/기간/자격/한도 불충족 (`content_locked`, `event_not_active`, `not_eligible`, `attempt_limit_reached`) | 유효 도전 전 도메인 거절; 시도권·Attempt 미생성 | 상태 안내, 자동 재시도 없음 |
| 입장 허용 후 콘텐츠 판정 실패 | 정상 게임 결과; Attempt와 시도권 차감 확정, 성장/미션/보상 미변경 | 게임 결과 표시, 새 도전은 새 request_id |
| 이미 처리된 request_id | 멱등 응답 | 기존 결과 반환 |
| 동일 request_id, 다른 payload (`idempotency_key_conflict`) | 요청 키 충돌 | 원 요청 상태를 확인하고 새 의도면 새 키 사용 |
| 같은 유저 상태에 대한 동시 수정 충돌 (`state_version_conflict`) | 경합 결과 | 최신 상태를 조회한 뒤 새 의도로 요청 |
| 승인 버전/게임 데이터 버전 불일치 (`source_version_conflict`, `catalog_version_unavailable`) | 적용 불가/충돌 | 운영 확인·재검증 |
| 일시 저장소/의존 시스템 장애 (`temporarily_unavailable`, `result_unknown`) | 서비스 실패 또는 조정 대기 | 같은 request ID로 상태 조회/재전송 |

오류 응답은 `code`, 안전한 `message`, `retryable`, `request_id`, `correlation_id`를 갖는다. `retryable=true`여도 클라이언트는 같은 명령을 새 키로 중복 제출하지 않고 동일 요청 ID의 상태를 확인한다.

복구 횟수·백오프·Safe Stop은 공통 오류 정책에서 결정한다. Runtime은 거절을 성공으로 위장하지 않고 부분 상태를 확정 응답하지 않는다.

## 보안·관측 요구

유저 및 운영자 권한을 서버에서 검증하고, 클라이언트 지정 ID만으로 승인 권한을 부여하지 않는다. 각 변경은 `request_id`, actor, event/result version, action ID, 데이터 버전으로 추적한다. 개인 정보와 불필요한 요청 원문은 로그에 기록하지 않는다.

## 완료 기준

- 엔드포인트별 인증/권한, 스키마, 성공/거절/충돌 응답을 확정한다.
- 중복 행동·동시 행동·이벤트 종료 경계의 처리 결과가 정해진다.
- 동일 키/동일 payload, 동일 키/다른 payload, 커밋 직후 응답 유실이 중복 상태 변경 없이 재현된다.
- 모든 행동 판정과 기록에서 실제 사용한 규칙/데이터 버전 및 서버 시각을 확인할 수 있다.
- API 변경과 DB 트랜잭션의 원자성 및 관측 식별자가 대응한다.

