# 작은 영웅의 이벤트 모험

`yejin` 운영 화면에서 생성·승인한 이벤트를 캐주얼 RPG 화면에서 확인하는 **로컬 플레이 데모**다. 운영 화면과 백엔드 코드는 수정하지 않고 독립된 `demo-game/`에서 실행한다. 로비, 일반 사냥, HP·포션·골드·경험치, 레벨업, Lv.3 던전 해금, 이벤트 미션, 가방을 제공한다.

## 실행

1. 백엔드를 실행한다. 빠른 로컬 확인은 저장소 루트에서 `WORKFLOW_ENGINE=backend_b_mock`으로 시작하면 된다. 실제 게임 데이터까지 연동하려면 팀 환경의 PostgreSQL·MCP 서버와 `WORKFLOW_ENGINE=backend_b_mcp` 설정을 사용한다.
2. `yejin` 운영 화면에서 이벤트를 생성하고 규칙 검증·AI 검수 결과를 확인한 뒤 **승인**한다. 승인 전 Task는 게임에서 거절된다.
3. 다른 터미널에서 게임 서버를 실행한다.

```powershell
$env:BACKEND_BASE_URL="http://127.0.0.1:8000"
python demo-game/server.py
```

브라우저에서 `http://127.0.0.1:8090`을 열어 **이벤트** 메뉴에 승인된 `TASK-XXXXXXXXXXXX`를 입력한다. 이벤트를 연결하지 않아도 일반 사냥과 레벨업은 체험할 수 있다. `DEMO_GAME_PORT`로 게임 포트를 바꿀 수 있다. 브라우저는 게임 서버와만 통신하고, 게임 서버가 백엔드의 `GET /api/tasks/{task_id}`를 조회하므로 CORS 설정은 필요 없다.

## 동작·제한

- `workflow_status=COMPLETED`, `approval_status=APPROVED`, `critic_status=PASS`, `rule_validation.is_valid=true`인 Task의 **`final_result`만** 플레이한다. 승인 대기·실패·거절·검증 실패 결과는 불러오지 않는다.
- 전투는 공격·몬스터 반격·포션 회복·전투 실패·재도전으로 진행된다. 몬스터 처치로 경험치와 골드를 얻고, 레벨업 때 HP·공격력이 증가한다. 던전은 Lv.3부터 입장할 수 있다.
- 이벤트의 `monster_kill`, `dungeon_clear`, `level_reach` 미션을 게임 행동에 연결한다. 일자별 미션을 마치면 승인된 보상을 가방에 시뮬레이션한다. 캐릭터와 Task ID·결과 버전별 이벤트 진행은 브라우저 `localStorage`에만 저장한다.
- 실제 계정·레벨·인벤토리·보상 지급 API는 없다. 게임 진행은 백엔드에 기록되지 않으므로 운영 결과나 다른 사용자의 데이터에 영향을 주지 않는다.
- 개발용 로컬 서버다. 인증 기능이 없으므로 외부에 공개하지 않는다.

테스트: `python -m unittest discover -s demo-game/tests -v`
