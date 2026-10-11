# Runtime Capability·MCP 데이터 계약

## 2026-10-11 오류 복구 연결

capability/planning context/rules/item 조회는 pinned 정책으로 bounded retry를 판단한다. game rules 운영 오류에만 검증된 fallback을 선택하고 그 실패도 같은 정책 루프에 재진입한다. primary+fallback의 시도 예산은 공유한다. 응답 JSON/필드/버전/digest 불일치는 `ERR-CONTRACT-MCP-001`로 종료하며 fallback이나 반복 요청으로 신뢰 문제를 우회하지 않는다. [공통 복구 계약 §6](15_오류대응정책_Workflow_연결_재검사.md#6-정책-연결-보완-구현)을 따른다.

## 1. 목적과 현재 상태

이 문서는 기획 Workflow가 가상 게임의 규칙·콘텐츠와 실제 적용 범위를 동일한 버전 기준으로 조회하는 계약을 정한다. MCP는 조회 transport이며, capability의 권위는 게임 Runtime의 버전 고정 카탈로그와 이벤트 적용 adapter다.

2026-10-11 구현 기준으로 새 canonical planning은 Runtime의 Opong snapshot(`opong-data-1.3.0`)과 Capability(`opong-capabilities-1.0.1`)를 기준으로 한다. 1.0.1은 Runtime이 실제 보존하는 reward grant policy와 per-user 지급 횟수를 capability constraint로 명시한다. 기존 `tinyheroes-1.0.0` 자료는 legacy Task 처리에만 남아 있으며 두 카탈로그의 동등성을 뜻하지 않는다.

2026-10-11 구현 기준으로 canonical planning은 `MCP_URL` 설정 시 Streamable HTTP의 `get_event_capabilities`, 요청 세그먼트의 `get_event_planning_context`, 지정 콘텐츠의 `get_game_rules`, 지정 보상의 `get_item_info`를 호출한다. 미설정 시에는 같은 Opong snapshot 기반 local adapter를 사용한다. 모든 결과는 Runtime snapshot ID·digest를 대조한 뒤 versioned Planner Handoff와 Plan evidence에 전달한다. 미등록 세그먼트는 근거 누락으로 기록하고 결정적 검증이 `NEEDS_DATA`를 반환한다. MCP timeout은 고정 복구 정책으로 재시도하고, Primary 콘텐츠 규칙 Tool 오류는 digest가 고정된 fallback을 시도한다. 재시도·fallback 결정과 성공은 Workflow trace에 기록한다. 복구 한도 초과는 `SAFE_STOP` Workflow/Trace로 DB에 저장하고 표준 오류 코드를 기록한다. 실제 Opong MCP 프로세스에 연결한 canonical API 통합 smoke가 통과했다.

이 연결은 Planner에게 조회 Tool 실행 권한을 주지 않는다. 결정적 Scope Checker와 Balance Rule 역할을 통해 Orchestrator가 read-only Tool을 호출하고, 검증된 반환 데이터만 Planner의 입력 계약으로 전달한다. 세그먼트 profile 및 콘텐츠/아이템 사실 조회는 연결됐지만, 모든 전투·성장·경제 영향 계산을 MCP 응답만으로 완료하는 것은 아니다. 해당 판단은 같은 snapshot의 Capability와 결정적 검증기에 남는다.

실제 tool 출력은 콘텐츠의 전투·보상 요약, 아이템 지급 가능 여부·잔액 한도, 세그먼트 profile과 선택지를 제공한다. 이는 버전 고정된 데이터 근거이며, 실유저 통계로 간주하지 않는다. 세그먼트별 예상치는 `mock_historical_aggregate` provenance를 유지한다. 모든 경제 영향 계산이 이 조회 결과만으로 가능한 것은 아니며 근거가 없는 영향은 unknown으로 남긴다.

## 2. 데이터 권위와 버전 계약

### 2.1 권위 원천

1. `backend/data/virtual_game_v1.json`의 검토된 카탈로그를 게임 Runtime seed의 원천으로 둔다.
2. 실행 중 Runtime은 PostgreSQL의 무결성 검사를 통과한 `game_data_snapshot_id` 카탈로그를 권위 데이터로 사용한다.
3. MCP는 해당 Runtime snapshot을 읽기 전용으로 조회한다. `mcp_server/data/tiny_heroes*.json`을 새 기획 생성의 권위 원천으로 사용하지 않는다.
4. 새 Brief와 Plan은 다음 버전 집합을 함께 고정한다.

```yaml
game_id: opong_heroes
domain_schema_version: "1.3.0"
event_schema_version: "1.0.0"
game_ruleset_version: opong-rules-1.3.0
game_data_snapshot_id: opong-data-1.3.0
runtime_capability_catalog_version: opong-capabilities-1.0.1
balance_policy_version: string
```

`runtime_capability_catalog_version`은 action mapping, event adapter 제약, 장소·대상·기간 지원 조합 중 하나라도 바뀌면 증가시킨다. 카탈로그를 조회할 때 요청 snapshot과 반환 snapshot이 다르면 결과를 조합하거나 검증을 계속하지 않고 `ERR-DATA-CONTEXT-001`/`NEEDS_DATA`로 종료한다.

### 2.2 과거 Task 호환

`tinyheroes-1.0.0`은 과거 Task의 승인 이벤트 적용을 위해서만 명시적 legacy adapter로 읽는다. 새 기획 생성에서는 사용할 수 없다. Legacy Task를 적용할 때도 기존 Task의 원본 version을 보존하고, Runtime adapter가 해석할 수 없는 미션·보상은 적용을 거부한다. 버전 문자열만으로 현재 카탈로그와 동일하다고 판정하지 않는다.

## 3. Capability 계약

Capability 결과는 Runtime adapter와 catalog의 지원 조합에서 생성한다. LLM이 capability 항목이나 지원 상태를 만들거나 변경하지 않는다.

```yaml
capability_catalog_version: opong-capabilities-1.0.1
game_context: { game_id: opong_heroes, game_data_snapshot_id: opong-data-1.3.0,
                game_ruleset_version: opong-rules-1.3.0, event_schema_version: "1.0.0" }
event_constraints:
  operation_modes: [finite]
  start_policy: immediate
  duration_days: { minimum: 1, maximum: 7 }
  audience_cardinality: 1
  audiences: [new_user, returning_user, all_user]
event_types:
  - event_type: growth_progression
    missions: [stage_clear, level_reach]
  - event_type: dungeon_challenge
    missions: [dungeon_clear]
  - event_type: special_dungeon
    missions: [special_dungeon_floor_clear]
venues: []
rewards: []
```

실제 응답은 위 예시의 `venues`, `rewards`, `missions`를 같은 Runtime snapshot에서 조회한 상세 객체로 채운다. 각 capability 객체에는 다음이 필수다.

| 필드 | 계약 |
|---|---|
| `capability_id`, `capability_catalog_version` | 안정 ID 및 registry 버전 |
| `kind`, `status` | `event_type`, `mission_action`, `venue`, `reward`, `schedule`, `audience`; 지원 상태는 `SUPPORTED`, `NEEDS_DEVELOPMENT`, `NEEDS_DATA`, `UNSUPPORTED_BY_POLICY` |
| `input_constraints` | 허용 metric·goal·target·filter·수량·기간·대상 조합 |
| `runtime_adapter_ref` | 실제 등록/진행 코드의 adapter ID와 버전. 미연결이면 지원으로 표시 금지 |
| `evidence_refs` | catalog snapshot, ruleset, validator 및 rule ID |
| `reason_code` | 지원 제한 또는 미확인 사유의 안정 코드 |

### 3.1 로컬 MVP의 지원 범위

현재 Runtime과 adapter가 확인하는 범위의 초기 capability는 다음으로 제한한다.

| 구분 | 지원 계약 | 제한/판정 |
|---|---|---|
| 기간 | 유한 이벤트, 즉시 시작, 1–7일 | 미래 시작·무기한 진행은 `NEEDS_DEVELOPMENT` |
| 대상 | `new_user`, `returning_user`, `all_user` 중 하나 | 한 이벤트에 한 세그먼트. 여러 세그먼트 동시 설정은 개발 필요 또는 사용자 대안 확인 |
| 스테이지 | `stage_clear` → Runtime `stage_cleared` | `HUNT_001` 또는 Runtime stage ID로 변환 가능한 경우만 허용 |
| 던전 | `dungeon_clear` → Runtime `dungeon_cleared` | 현재 일반 던전 ID/레거시 alias의 명시 매핑만 허용 |
| 성장 | `level_reach` → Runtime `growth_milestone_reached` | metric은 `hero_level`; 임계값과 상한은 동일 snapshot에서 검증 |
| 특별 던전 | `special_dungeon_floor_clear` → Runtime `special_dungeon_progressed`의 `floor_cleared` signal | canonical Capability에 등록된 floor ID와 adapter만 허용한다. `monster_kill` legacy alias는 canonical Capability에 노출하지 않으며 새 Plan의 floor-clear 의미로 사용하지 않는다. |
| 장소/template | Capability에 등록된 `venue.<template_id>` 조합만 지원 | `template_id`, `layout_id`, `theme_id`, `difficulty_id`가 동일 snapshot의 등록값과 일치해야 한다. 데이터에 template은 있지만 venue binding이 없으면 `NEEDS_DEVELOPMENT`; template 자체를 snapshot에서 찾지 못하면 `NEEDS_DATA`로 판정한다. |
| 보상 | 카탈로그 item ID와 정수 수량, `once_per_user` 1회 지급 | item kind, max balance, 지급 source·한도와 `grant_policy`/`max_grants_per_user`를 검증. `once_per_cycle` 또는 1회 초과 지급은 Runtime이 의미를 보존하지 않으므로 `NEEDS_DEVELOPMENT`; 환산 규칙 없는 스킨/코스튬은 화폐 가치 계산 금지 |

콘텐츠 접근성은 같은 snapshot의 progression `start_level`/`max_level`, segment `min_level`/`max_level`, 콘텐츠 `entry_level`만으로 판정한다. 시작 시점 보장 레벨 이하의 콘텐츠는 규칙 기반 접근 가능, segment 최고 레벨을 초과하는 콘텐츠는 `MISSION_SEGMENT_LEVEL_UNREACHABLE` finding으로 Runtime 적용을 차단한다. 그 사이 레벨은 이벤트 기간 성장 속도 자료가 없어 `unknown`으로 표시한다. 현재 snapshot은 일별 기대 레벨 곡선을 제공하지 않으며, 이 결과를 전투 승률·완료 확률 또는 실유저 성과로 해석하지 않는다.

Runtime event 저장 형식은 day를 연결 키로 사용한다. canonical adapter는 Plan의 action 순서대로 각 미션에 고유 day를 부여하고 reward의 `mission_id`를 같은 day로 변환해 현재 mission-reward link를 보존한다. Runtime은 미션 완료 후 보상을 한 번 지급하고 grant policy와 max grants 필드를 저장하지 않는다. Capability 1.0.1은 `once_per_user`와 `max_grants_per_user=1`만 지원으로 선언한다. `once_per_cycle` 또는 1회 초과 지급은 실행에 넣지 않고 `NEEDS_DEVELOPMENT`로 반환한다. legacy day mapping에서 여러 미션이 같은 day에 들어오는 경우 첫 미션 자동 선택으로 연결 의미가 바뀔 수 있으므로 canonical 경로에서 이를 허용하지 않는다.

## 4. 결정적 게임 규칙·밸런스 입력

`get_game_rules`와 `get_event_planning_context`는 같은 snapshot에서 다음 정보를 제공한다.

- stage/dungeon/special dungeon ID·이름·종류·해금 조건·반복/진행 규칙. 무한 `stage_NNNN`와 `dungeon_floor_NNNN` ID는 Runtime과 같은 생성 규칙으로 조회한다.
- action signal과 판정 단위(전투 승리, 던전 클리어, 특별 던전 층 완료, 영웅 레벨)
- 목표 가능한 metric, 목표 연산자와 허용 범위, reset/기간 의미
- 콘텐츠별 전투 스탯·1:N 적 파티·경험치·재화 보상 및 반복 진행 formula 참조
- 성장 action별 비용·효과·상한과 세그먼트/유저 profile 입력
- item ID·kind·최대 보유량·지급 가능 여부·상점 가격/전환 규칙(있는 경우)
- 조회 근거의 `game_data_snapshot_id`, `game_ruleset_version`, source, data digest

계산기는 같은 버전 입력에 대해 같은 결과를 반환하고 `method_ref`와 입력 evidence를 남긴다. 규칙상 보상·성장량은 계산할 수 있으면 `calculated`다. 세그먼트 기준 중 `mock_historical_aggregate`는 가정 fixture로 표시하며 실유저 달성률이나 실측 평균으로 사용하지 않는다. 필요한 실측 자료가 없으면 실제 이용자 성공률은 `unknown`이다.

## 5. MCP Tool 계약

모든 Tool은 읽기 전용이고 `tool_id`, `tool_version`, `input_schema`, `output_schema`, 호출 허용 Agent/task, `timeout_seconds`, `retry_class`, 안정 오류 코드가 registry에 등록된다. Tool 결과는 사용자 지시가 아닌 untrusted data로 검증한다.

### 5.1 `get_event_capabilities`

입력: `game_id`, 필수 `game_data_snapshot_id`, 선택 `capability_catalog_version`. 출력: §3의 전체 범위/조합 및 버전. 누락 버전을 최신으로 묵시 치환하지 않는다. Scope Checker와 Validator가 호출할 수 있고 Planner는 결과 참조만 받는다.

### 5.2 `get_event_planning_context`

기존 `target_user`, `duration` 입력을 versioned contract로 확장한다. 입력은 `game_id`, `game_data_snapshot_id`, `target_segment_id`, `duration_days`다. 출력은 전체 raw catalog 복사본이 아니라 선택된 세그먼트의 고정 profile, 기간에 적용되는 콘텐츠/action 선택지, item 요약, 해당 snapshot·digest, synthetic/observed data provenance를 포함한다. 현재 Opong profile은 세그먼트별 최소/최대 레벨만 제공하며 일별 성장 곡선은 없다. 따라서 `duration_days`는 선택지 범위에는 쓰지만 그 기간 내 접근 가능성을 증명하지 않는다. 필수 snapshot ID 불일치나 세그먼트 미존재는 명시 오류다.

### 5.3 `get_game_rules`

입력은 `game_data_snapshot_id`, `content_id`다. 출력은 콘텐츠 ID·종류·이름·unlock 조건·행동 signal·목표 판정 규칙·전투/반복 progression·보상 계산 참조·snapshot/evidence다. 반복 스테이지와 던전 층은 Runtime generator로 산출해 실제 `combat`와 적 파티를 반환한다. 기존 이름·입장 레벨 전용 응답으로 밸런스 검증을 완료할 수 없다.

기획 validator는 같은 snapshot의 `hero_stats`와 콘텐츠 전투 규칙을 사용해 콘텐츠 입장 레벨 또는 segment 최소 레벨 및 segment 최고 레벨의 기본 성장(장비 강화 0) 1회 전투 결과를 결정적으로 계산한다. 단일 적은 `resolve_combat`, 적 파티는 `resolve_party_combat`과 같은 함수를 사용한다. 결과에는 `mission_combat_feasibility`, `opong-deterministic-combat-base-stats.v1`, snapshot·content·segment evidence를 기록한다. 이 계산은 특정 레벨과 기본 스탯에서의 규칙상 승패만 말한다. 장비 강화 분포나 사용자 레벨 분포가 없으면 실유저 승률·이벤트 기간 달성 확률을 산출하지 않는다.

### 5.4 `get_item_info`

입력은 `game_data_snapshot_id`, `item_id`다. 출력은 존재 여부·종류(currency/material/consumable/chest/skin 등)·max balance·event grant eligibility·quantity 제한·가격 및 전환 규칙(등록된 경우)·evidence다. 가격/가치 규칙이 없는 cosmetic을 재화로 임의 환산하지 않는다.

### 5.5 `get_game_rules_fallback`

Fallback은 같은 `game_data_snapshot_id`, `game_ruleset_version`, `event_schema_version`, capability version과 digest를 가진 검증 snapshot만 반환한다. primary와 동등성이 하나라도 확인되지 않으면 fallback을 쓰지 않고 `NEEDS_DATA`/dependency error로 종료한다. 같은 버전 문자열만 일치하는 것은 충분하지 않다.

### 2026-10-11 적용 버전 갱신

신규 기획은 `opong-data-1.4.0`과 `opong-capabilities-1.0.2`를 기본으로 사용한다. Capability 1.0.2는 기존 유한 운영과 함께 `ongoing` 운영을 지원으로 선언한다. `1.0.1`은 저장된 과거 결과를 재현할 수 있도록 finite 전용 호환 계약으로 계속 제공한다. 각 응답의 `game_context` capability version은 요청한 정확한 catalog version과 일치해야 한다. Runtime 데이터 카탈로그는 종료일 없는 운영 이벤트와 전 기간 층별 미션을 `event_schema_version=1.1.0`으로 저장한다. 세부 층별 조건은 [이벤트 적용 계약](../게임설계/05_이벤트적용_계약.md#구현-반영-2026-10-11)을 따른다.

### 5.6 외부 사례 자료

`get_clash_event_sample`은 공개 표본의 참고 자료로 분리한다. `source_game_id=clash_of_clans`와 표본 선택·수집 시각을 보존하며 Opong capability/evidence로 사용하지 않는다. 로컬 MVP에서는 Runtime 적합성 판정의 입력으로 비활성화한다.

## 6. 오류·안전·권한

| 상황 | 결과 |
|---|---|
| 요청 snapshot을 찾을 수 없음 | `ERR-DATA-CONTEXT-001`, `NEEDS_DATA`; 다른 버전으로 자동 전환 금지 |
| 콘텐츠/아이템 ID가 snapshot에 없음 | 미존재와 조회 장애를 구분해 `NEEDS_DATA` 또는 규칙 finding 반환 |
| catalog는 있으나 adapter mapping이 없음 | `NEEDS_DEVELOPMENT`, `runtime_eligible=false` |
| action 의미가 Runtime signal과 다름 | 지원 판정 금지; 예: kill count를 floor clear로 변환하지 않음 |
| fallback provenance/digest 불일치 | fallback 금지, `SAFE_STOP`/`NEEDS_DATA` |
| MCP timeout/throttle | 해당 read tool만 정책 budget 내 retry; 버전이 다른 fallback 금지 |
| MCP write 요청 | tool catalog에 write가 없으므로 거부. 업무 변경은 Controller→내부 서비스/API→DB transaction 경로만 사용 |

## 7. 전환 및 동기화 기준

1. Runtime과 MCP가 같은 snapshot을 읽는 adapter를 확정한다.
2. 새 이벤트 생성은 canonical Opong snapshot과 capability version만 사용한다.
3. Legacy `tinyheroes-1.0.0`은 저장된 과거 Task만 처리하는 명시적 변환 경로로 제한한다.
4. Runtime 적용 adapter가 Plan의 action, venue, audience, reward grant, explicit mission-reward link를 손실 없이 표현할 때만 `SUPPORTED`로 올린다.
5. Catalog 또는 adapter 변경 시 관련 version을 올리고, 기존 Plan/승인/실행은 이전 버전을 유지한다.

## 8. 완료 기준

- MCP의 모든 기획 데이터 응답이 Runtime과 동일한 snapshot·ruleset·schema 버전을 가리킨다.
- 지원 capability 목록이 실제 Runtime adapter 제약과 일치하고 별도의 LLM 추측이 개입하지 않는다.
- 콘텐츠·아이템·세그먼트·전투·성장·보상 계산에 필요한 근거가 같은 snapshot에서 조회된다.
- synthetic cohort 값이 실측값처럼 취급되지 않는다.
- legacy source, fallback, 외부 Clash 표본이 현재 Runtime capability와 섞이지 않는다.
- canonical에서는 `monster_kill`/floor clear 의미 혼동, 불명확하거나 불일치한 mission-reward link, Runtime이 보존하지 않는 grant policy를 지원으로 통과시키지 않는다.
- 읽기 전용 Tool allowlist와 MCP write 거부가 명확하다.

## 9. 참조

- [기획안·게임 기능 계약](01_기획안_게임기능_계약_설계.md)
- [멀티 에이전트·Workflow](02_멀티에이전트_Workflow_설계.md)
- [API·저장·Trace](06_API_저장_Trace_설계.md)
- [게임 데이터 Schema](../게임설계/02_게임데이터_스키마.md)
- [게임 Runtime/API](../게임설계/04_게임Runtime_API.md)
- [이벤트 적용 계약](../게임설계/05_이벤트적용_계약.md)
- [게임 검증·밸런스](../게임설계/07_게임검증_밸런스.md)
