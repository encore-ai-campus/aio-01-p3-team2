# 게임 데이터 DB 구조 (초기 설계 참고용)

공용 DB의 실제 테이블 생성과 초기 데이터 적용은 Backend A가 담당합니다. 이 문서와 같은 폴더의 SQL은 초기 초안이며, **별도로 실행하지 않습니다.** MCP 조회 코드가 기대하는 열은 `game_id`, `game_data_version`, `data`입니다.

```mermaid
erDiagram
    GAME_DATA_CATALOG {
        TEXT game_id PK
        TEXT game_data_version
        JSONB data
        TIMESTAMPTZ updated_at
    }
```

| 열 | 역할 |
| --- | --- |
| `game_id` | 게임 식별자. MVP에서는 `tiny_heroes` 한 건입니다. |
| `game_data_version` | 조회 결과와 Trace에 남길 데이터 버전입니다. |
| `data` | 기존 JSON의 `contents`, `items`, `user_segments`를 그대로 담습니다. |
| `updated_at` | 마지막 초기 데이터 적용 시각입니다. |

초안의 `001_create_game_data.sql`은 테이블 정의, `002_seed_game_data.sql`은 초기 데이터 한 건을 담습니다. 공용 DB에는 Backend A의 적용 도구만 사용합니다. `data`는 JSONB이므로 콘텐츠·아이템은 별도 관계형 테이블이나 ERD 연결선으로 표현되지 않습니다.

백엔드의 작업·Trace 테이블과 물리적 외래키는 없습니다. 백엔드는 MCP 응답의 `game_data_version`과 `source`를 기록해 검증에 사용한 데이터의 출처를 남깁니다. 이 폴더의 SQL은 운영 기록 테이블을 만들거나 변경하지 않습니다.
