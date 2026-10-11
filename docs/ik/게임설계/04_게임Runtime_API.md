# 게임 Runtime·API 설계서

## 문서 상태와 책임

- 상태: /api/game/* 구현. WP-08에서 HP 전투·사냥 상태·성장 요청을 확장했으며 브라우저 수용은 진행 전
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
| 보관함 조회 (구현) | `GET /api/game/inventory?limit=20` | 활성 재화/재료 잔액, 보유 스킨 ID와 장착 상태, 최근 원장 |
| 스킨 상점 조회 (구현) | `GET /api/game/shop/offers` | 고정 스킨 상품, 가격, 보유/구매 가능 상태 |
| 스킨 구매 (구현) | `POST /api/game/shop/purchases` | GEM 차감, 소유 스킨, 구매 영수증 및 갱신 잔액 |
| 스킨 장착 (구현) | `POST /api/game/skins/equip` | 유저가 소유한 스킨만 장착하거나 해제 |
| 로컬 특별 던전 fixture (구현) | `POST /api/game/demo-fixtures/special-dungeon` | loopback 전용 게임 단독 시연 이벤트. 승인 이벤트와 구분 |
| 콘텐츠 목록 | `GET /api/game/catalog/{content_type}` | 활성 콘텐츠와 입장 조건 |
| 이벤트 적용 | `POST /api/game/events/apply` | 적용 이벤트 ID·상태·설정 버전·영수증 |
| 이벤트 조회 | `GET /api/game/events/{event_id}` | 기간·상태·venue·진행 요약 |
| 플레이 행동 | `POST /api/game/actions` | 서버 판정, 행동 ID, 갱신 상태 |
| 왕국 로비 성장 | POST /api/game/growth-actions | GOLD 비용·전후 능력치·잔액과 성장 원장 ID가 서버에서 확정된 결과 |
| 이벤트 진행 조회 | `GET /api/game/events/{event_id}/progress` | 고정 데모 유저의 미션별 진행·완료·보상 상태 |

2026-10-11 이벤트 던전 계약 구현: 기획 UI는 `GET /api/game/catalog/special_dungeon_templates`와 `GET /api/game/catalog/items`에서 표시용 카탈로그를 읽는다. 승인된 Plan은 `POST /api/game/events/apply`로 등록되고, 층별 first-clear 진행·보상 결과는 `GET /api/game/events/{event_id}/progress`에서 읽는다. `operation_mode=ongoing` 응답은 `end_at: null`이며 해당 이벤트 mission은 day 값과 무관하게 active 기간 전체에서 진행된다. 행동은 기존 `POST /api/game/actions`의 성공한 `special_dungeon_progressed/floor_cleared` ActionRecord로 판정한다. 필드의 근거와 버전은 [이벤트 적용 계약](05_이벤트적용_계약.md)을 따른다.

2026-10-11 선택 공간 후속 변경: 신규 `opong-data-1.5.0` 이벤트에서는 `GET /api/game/catalog/special_dungeon_templates`의 `spaces[]`와 `space_id`를 사용한다. 적용 응답의 `venue.space_ids`가 해당 이벤트의 유일한 입장 허용 목록이다. `POST /api/game/actions`에서 선택하지 않은 공간을 요청하면 `409 content_locked`를 반환하며, 성공 행동은 `special_dungeon_progressed/space_cleared`로 기록된다. 기존 floor 이벤트 응답은 저장된 구버전 계약으로 조회하고 일반 던전 층 API 동작은 유지한다.
| 명령 결과 조회 | `GET /api/game/commands/{request_id}` | 최초 요청의 처리 상태와 저장된 결과 |

현재 구현 경로는 /api/game/actions 및 /api/game/growth-actions를 포함한다. 로컬 MVP는 단일 고정 데모 유저를 사용하며 user_id를 행동 요청에서 받지 않는다. 별도 사용자 인증은 없고 API 및 UI는 loopback 요청으로 제한한다. 이는 로컬 데모 경계이며 외부에 노출하면 안 된다.

논리 HTTP 결과는 다음과 같이 통일한다: 조회 성공과 도전 판정 완료(게임상 성공/실패 모두)는 `200`; 비동기 적용 또는 아직 처리 중인 명령은 `202`; 잘못된 구조/타입은 `400`; 존재하지 않거나 고정 스냅샷에서 비활성인 리소스는 `404`; 자격·기간·잠금·시도 한도 등 도메인 사전조건 거절은 `422`; 같은 멱등 키의 다른 payload·source digest 충돌·상태 버전 충돌은 `409`; 일시 의존 시스템 장애는 `503`으로 표현한다. 실제 API prefix와 공통 error envelope만 기존 Backend 규약에 맞춰 매핑한다.

## 요청·응답 논리 모델

행동 명령과 응답의 논리 계약은 아래와 같다. 이는 필드 의미를 확정하며 물리 OpenAPI/JSON Schema 문법은 구현 계획에서 변환한다. 자동 스테이지 전투는 매 전투를 식별 가능한 별도 명령으로 기록하고, 승리/패배에 따른 다음 사냥 지점은 서버 상태에서 반환해야 한다. 클라이언트는 연출을 이어갈 수 있지만 진행 위치를 결정하지 않는다.

```json
{
  "request_id": "<unique id>",
  "command_type": "stage_challenge | dungeon_challenge | special_dungeon_challenge",
  "content_id": "<catalog id>",
  "difficulty_id": "<required for ordinary dungeon>",
  "runtime_event_id": "<optional event association>",
  "stage_mode": "push | farm (stage only)"
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
  "stage_progress": {
    "result": "succeeded | failed",
    "highest_cleared_stage": "<stage id>",
    "hunt_mode": "push | farm",
    "next_hunt_stage": "<next stage on push win, highest cleared stage on push loss or farm win>",
    "next_action": "continue_automatically | await_player"
  },
  "combat": {
    "combat_log": [
      {
        "sequence": 1,
        "actor": "hero | monster",
        "action": "attack | hit | defeated",
        "damage": 0,
        "target_hp_after": 0
      }
    ],
    "hero_hp_after": 0,
    "monster_hp_after": 0,
    "reason_code": "<terminal result reason>"
  },
  "ruleset_version": "<version>",
  "data_snapshot_id": "<snapshot>"
}
```

`command_status`는 명령 처리 상태이며 도전 판정 결과와 구분한다. `completed` 응답의 Attempt `result`가 `succeeded` 또는 `failed`일 수 있다. 도전 실패는 정상 게임 결과로 Attempt/시도권만 확정한다. 해금·기간·자격·시도권 불충족은 `rejected`이며 유효 도전 시도를 만들지 않는다. 요청이 진행 중이면 `processing` 상태와 같은 요청 ID로 조회할 수 있는 결과 참조를 반환한다. 서비스 오류는 별도 오류 envelope에 안정된 `code`, 사용자 안전 설명, 재시도 가능 여부, 상관관계 식별자를 제공한다. 비밀·내부 stack trace는 응답하지 않는다.

재화/아이템이 지급되는 응답은 보상 원장과 최신 유저 잔액을 함께 반환한다. 화면 표시용 `inventory_changes`는 item ID, delta, 처리 전/후 잔액, 지급 출처(`content_clear` 또는 `event_mission`)를 나타낸다. 화면은 이 확정 delta를 `+N` 획득 연출에 사용하고, 최종 HUD 잔액은 응답의 새 잔액으로 맞춘다. 현재 구현이 전/후 delta를 별도 필드로 주지 않으면 직전 조회 값과 응답 잔액의 차이를 연출에 쓸 수 있으나, 그 차이를 서버 판정이나 원장으로 취급하지 않는다.

이벤트 진행 조회는 이벤트 ID·유저별 참여 상태, 이벤트 버전, 각 미션의 `current_value`, `target`, `status`, 완료 시각, 연결된 RewardRecord와 현재 가상 인벤토리 상태를 반환한다. 최근 20개 Attempt 및 30개 Action의 식별자·결과·콘텐츠도 운영 확인을 위해 포함한다. 읽기 응답은 권위 저장 상태에서 구성한다.

`user_id`는 본문 값만으로 인증하지 않는다. 인증 주체에서 식별자를 결정하고 본문에 값이 오더라도 일치 여부를 검사한다. 같은 사용자와 명령 범위에서 같은 `request_id`를 다시 보내면 최초 명령 결과를 반환한다. 이미 같은 키가 다른 payload hash로 처리되었으면 충돌을 반환하고 새 명령으로 실행하지 않는다. 클라이언트는 결과가 불명확한 네트워크 오류 뒤에 새 키를 만들지 않고 원 요청 ID로 결과를 조회/재전송한다.

서버는 다음 순서로 처리한다: 인증/입력 검증 → 멱등 키와 payload hash 확인 → 처리 중/완료된 명령 확인 → 단일 규칙·데이터 버전 고정 → 상태 및 참여 자격 읽기 → 입장·한도 판정 → 결정적 게임 결과 계산 → 행동/성장/미션/보상과 멱등 결과 원자 적용 → 결과 반환. 같은 유저 상태에 대한 동시 행동은 DB 잠금 또는 낙관적 버전 검사로 직렬화/충돌 처리해야 한다. 충돌로 거절된 요청은 판정을 다시 해야 한다면 동일 멱등 키의 결과를 덮어쓰지 않고 API 정책에 맞는 명시 응답을 반환한다. 저장소 경계에 따라 일부 작업이 분산되면 outbox/재조정 방식 등 최종 기술을 선택해야 한다.

자동 사냥의 각 전투는 요청 ID·Attempt·Action 단위로 중복 실행을 막는다. 승리는 다음 순차 스테이지를 다음 사냥 대상으로 반환하고 최고 클리어를 갱신한다. 패배는 기존 최고 클리어 스테이지를 다음 사냥 대상으로 반환하며, 아직 클리어 기록이 없으면 카탈로그 첫 스테이지를 반환한다. 로비 강화 요청은 허용된 강화 정의를 참조하며, 재화 차감과 전투 능력치 갱신을 한 원자 처리로 저장한다. 공격 애니메이션 횟수나 클라이언트 타이머가 전투·강화 상태를 확정하지 않는다.

전투 응답에는 화면이 결과와 일치하게 재생할 수 있도록 순서가 고정된 `combat_log`를 포함한다. 스테이지는 기존 단일 `monster` 필드와 영웅 공격 후 생존 몬스터 반격 규칙을 유지한다. 일반 던전 응답은 `combat.enemies[]`의 적별 ID/역할/시작·최종 HP/생존 여부와 파티 전투 로그를 반환한다. 던전 한 라운드는 영웅이 가장 앞선 생존 적을 공격한 뒤, 파티가 남아 있으면 고정 순번상 다음 생존 적 한 명만 반격한다. 쓰러진 적은 순번에서 건너뛴다. 모든 적이 쓰러지면 `succeeded`, 영웅 HP가 0이면 `failed`다. 양측 HP는 0보다 작아지지 않는다. 로그와 파티 최종 상태·승패는 같은 서버 판정 결과에 속하고 멱등 응답에 저장되어야 한다. 클라이언트는 이 로그를 시간축에 맞춰 재생하며 임의 타격/피해 이벤트를 추가하지 않는다. 로그/응답이 유실되면 같은 `request_id`의 기존 결과를 조회한다.

Runtime은 서버가 저장한 `hunt_mode`를 기준으로 한다. `push` 승리 후 다음 무한 stage를 열고, 패배는 `farm`으로 전환해 최고 클리어 위치를 사냥한다. `farm` 승리는 같은 stage를 유지한다. 던전도 별도로 push/farm 상태를 저장하고 무한 층으로 생성한다.

행동 결과 시간은 서버가 부여한다. 이벤트 기간은 UTC의 `[start_at, end_at)`로 비교하고, 일일 시도권은 규칙 버전의 명시적 시간대·초기화 경계로 계산한다. MVP 권위 판정은 난수 없이 결정적으로 수행한다. 난수를 추가하는 변경은 규칙 버전 변경으로 취급하고 seed/알고리즘 재현 정보를 행동 기록에 남기기 전에는 허용하지 않는다.

명령 처리 상태의 논리 모델은 `received → processing → completed | rejected | retryable_failure`다. `completed`는 콘텐츠 도전 판정(성공 또는 게임상 실패)이 확정됐음을 뜻한다. `rejected`는 입장 불가·기간 종료 등 도메인 사전조건 거절로 재시도하지 않는다. `retryable_failure`는 원자 커밋 전 일시 장애처럼 안전 재시도가 확인된 경우에만 사용한다. 커밋 결과가 불명확하면 `reconciling`을 거쳐 같은 키의 상태를 조회해 중복 행동/보상을 막는다. 외부 이벤트 적용 상태는 05 문서의 별도 계약을 따른다.

### WP-08 구현 응답 필드

POST /api/game/actions는 선택적으로 stage_mode를 받는다. 스테이지 응답은 `combat.result`, `hero_hp_start/after`, 단일 `monster_hp_start/after`, `combat_log` 및 `stage_progress`를 포함한다. 일반 던전 응답은 `combat.enemies[]`(enemy_id, role, name, visual_asset_id, hp_start, hp_after, defeated), `combat_log`(actor_id, target_id 포함)와 `dungeon_progress`를 포함한다. 던전 파티는 기본 3명이며 5층 배수는 보스 1명과 호위 2명이다. `dungeon_progress`는 result, highest_cleared_floor, hunt_mode, next_floor_id, next_action을 반환한다. 전투 Attempt 결과는 성공/실패 모두 저장하며 실패에는 성공 ActionRecord·경험치·보상이 없다. 1:N 형태는 일반 던전용이며 특별 던전은 자체 템플릿 계약을 따른다.

POST /api/game/growth-actions 본문은 request_id와 growth_action_id다. 성공 응답은 growth_record_id, quantity_spent, inventory_changes(음수 delta 및 전후 잔액), stats_before/after, 최신 user 상태와 버전을 반환한다. 동일 요청 ID 재전송은 최초 응답을 돌려준다. 클라이언트는 네트워크 오류나 5xx에서 GET /api/game/commands/{request_id}로 결과를 확인하고, 결과가 404일 때만 같은 본문과 request_id로 재전송한다. 강화 비용과 능력치는 카탈로그에서 판정한다.

## WP-09 API 구현

현재 API 계약은 유한 stage/dungeon 카탈로그와 stage `stage_mode`를 기준으로 한다. 연속 진행을 구현할 때 stage/floor 인덱스는 클라이언트가 선택지로 보낼 수 있지만 서버는 저장된 최고 기록과 다음 진행을 다시 검증한다. 콘텐츠 ID는 순번 기반 생성 ID로 해석한다. 폐광 명령은 별도 `dungeon_mode=push|farm`을 사용하고 성공/패배 응답에 `highest_cleared_floor`, `current_floor`, `hunt_mode`, `next_action`을 포함한다. 카탈로그 종료 sentinel 대신 프로필 계산 실패/수치 표현 범위 초과를 명시적 오류로 반환한다.

보관함 조회는 서버 권위 `VirtualInventory`, 스킨 소유/장착 상태, 최근 `InventoryLedgerEntry`를 반환한다. ledger 응답은 `entry_id`, `item_id`, `delta`, `balance_before`, `balance_after`, `source_type`, `source_id`, `occurred_at`을 포함한다. 페이지/limit은 조회량 제한이며 보관함에 표시할 원장 합계나 잔액을 임의로 자르지 않는다. 스킨 장착은 보유 여부와 허용 asset ID를 서버에서 확인하고 유저의 `equipped_skin_id`를 갱신한다.

스킨 상점은 카탈로그의 고정 상품 3개를 반환하며 각 상품은 1 GEM이다. 구매 요청은 `request_id`와 `offer_id`를 받는다. 서버는 중복 소유와 GEM 잔액을 확인하고, 차감·고유 스킨 소유·구매 영수증·원장 delta를 한 transaction에 기록한다. 재요청은 최초 결과를 반환한다. 로컬 특별 던전 fixture endpoint는 loopback 요청에만 응답하며 별도 `LOCAL-FIXTURE-*` source로 식별된다.

전투/강화 응답은 XP 및 재화 delta를 포함한다. 강화는 GOLD와 해당 rank 이정표의 MOON_SHARD를 함께 차감한다. 첫 stage 5·10·15 클리어는 GEM milestone을 지급하고 고정 스킨 구매가 GEM을 소비한다. 미션별 반복 주기, 운영 이벤트의 경제 영향 평가는 WP-10 범위다.

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

