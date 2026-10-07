# Opong Heroes MCP 서버

게임 이벤트 기획·검수에 필요한 작은 Mock 게임 데이터를 조회합니다. 게임 규칙의 최종 판정, 재시도, Primary에서 Fallback으로의 전환은 백엔드가 담당합니다.

## 데이터와 Tool

| Tool | 입력 | 조회 파일 | 주요 결과 |
| --- | --- | --- | --- |
| `get_game_rules` | `content_id` | `data/tiny_heroes.json` | 콘텐츠 이름·필요 레벨·유저 성장 기준, `source=primary` |
| `get_game_rules_fallback` | `content_id` | `data/tiny_heroes_fallback.json` | 같은 형식의 검증된 스냅샷, `source=fallback_snapshot` |
| `get_item_info` | `item_id` | `data/tiny_heroes.json` | 아이템 이름·존재 여부, `source=primary` |

모든 결과에 `game_data_version`이 있습니다. 없는 ID는 오류 대신 `exists=false`와 `null` 이름·레벨을 반환합니다. 규칙 조회 결과는 없는 ID여도 `user_segments`를 포함합니다. JSON 파싱 실패, 파일 누락, 필수 데이터 오류는 Tool 오류로 전달합니다. 데이터는 호출마다 읽으며 서버 안에서 재시도하지 않습니다.

MCP SDK의 `call_tool` 결과에서는 반환한 dict가 `result.content[0].text`의 JSON 문자열로 전달됩니다. 백엔드는 `isError`를 확인한 뒤 이 문자열을 JSON으로 해석해야 합니다.

## 로컬 실행

저장소 루트에서 실행합니다.

```powershell
python -m pip install -r mcp_server/requirements.txt
python -m mcp_server.server
```

기본 주소는 `http://127.0.0.1:8010/mcp`입니다. 로컬 환경변수 이름은 `.env.example`에 있습니다. 예시 파일은 자동으로 읽지 않으며, 필요한 경우 실행 환경에서 변수를 설정합니다.

```powershell
python -m pip install pytest
python -m pytest mcp_server/tests -q
```

백엔드의 준비 상태 확인은 MCP 연결·초기화 후 `get_game_rules(content_id="HUNT_001")`를 실제 호출하면 데이터 읽기까지 확인할 수 있습니다.

## Docker 빌드

Docker 빌드 컨텍스트는 **`mcp_server/`**로 지정합니다.

```powershell
docker build -f mcp_server/Dockerfile -t opong-mcp ./mcp_server
docker run --rm -e MCP_HOST=0.0.0.0 -p 8010:8010 opong-mcp
```

Compose 내부에서는 팀이 정한 MCP 서비스 이름과 `8010/mcp` 경로로 백엔드가 접근합니다. 데이터 변경 시 기본 JSON과 Snapshot의 버전을 확인하고 테스트한 뒤 이미지를 다시 빌드합니다.
