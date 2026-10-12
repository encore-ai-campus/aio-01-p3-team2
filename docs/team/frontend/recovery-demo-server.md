# 복수 사용자 복구 시연 서버

2026-10-12, `fix/recovery-ui-sync` 분리 작업 디렉터리에서 실행했다.

- 접속 주소: `http://192.168.1.48:5184/` (서버 PC Wi-Fi 주소, 주소 변경 시 다시 확인)
- 이 PC 접속: `http://localhost:5184/`
- 로그인 버튼 → **복구 시연** → 그래프의 노드 → 오류 선택 → **선택한 오류 실행** → **실행 경로 보기**
- 프론트엔드 5184만 LAN에 바인딩한다. API 8183, MCP 8184는 loopback에 유지한다. 두 API 프록시 모두 `eventquest_fault_test` 시험 DB를 사용한다.
- 실제 PostgreSQL/HTTP MCP, 결정적 Agent 모드이며 외부 LLM 호출은 없다.
- 현재 반영된 카탈로그는 `fault-lab-v1.3`이다. 새 시험의 정상 결과는 최종 검증 → 저장 완료 → Supervisor 전이 → 운영자 승인 대기 순서로 표시된다. LAN LLM timeout 시험 `09174f94-6e2b-4a27-9237-1bcf461802f2`에서 이 순서와 PASS를 확인했다. 기존 저장된 시험은 원래 순서를 보존하므로 새로고침 후 새 시험을 실행한다.

## 사용자 실행 제약

브라우저별 sessionStorage로 현재 시험 결과가 구분된다. 두 개의 독립 브라우저 컨텍스트에서 동시에 화면 접속하고, 한 사용자의 시험 결과가 다른 사용자 화면에 자동으로 나타나지 않는 것을 확인했다. 계정별 인증이나 결과 접근 제어를 제공하는 환경은 아니다.

공유 시험 DB의 정책 복원과 데이터 검증을 위해 오류 주입은 한 번에 한 건씩 실행한다. 다른 사용자가 실행 중이면 BUSY 응답이 나타날 수 있으므로 완료 후 실행한다. 동시 오류 주입 부하 시험을 위한 독립 사용자 환경은 아니다.

LAN HTTP에서는 `crypto.randomUUID`가 제공되지 않아 복구 시연의 요청 ID를 `crypto.getRandomValues` 기반 UUID로 생성한다. 서버는 현재 IPv4 주소들을 허용 Origin으로 지정한다. LAN 주소에서 실제 주입 시험 `cb8e48b0-c519-4809-b1ba-d35d08e68a79`가 COMPLETED/PASS, cleanup=true로 완료됐고 브라우저 오류는 없었다. 이 검사는 서버 PC에서 LAN 주소를 사용했으며 다른 물리 PC의 접속을 확인한 것은 아니다.

## 실행 및 종료

저장소 루트에서 아래 명령으로 API/MCP와 UI를 시작한다. 기존 시험용 PostgreSQL 컨테이너와 Python 환경, frontend-web의 설치된 의존성이 필요하다.

```powershell
./infra/fault-lab-demo.ps1
./infra/fault-lab-demo.ps1 -Stop
```

로그와 PID 파일은 Git에 포함하지 않는 `backend/test-results/fault-lab/`에 저장한다. 종료는 BUSY/DIRTY 상태에서 차단된다.

## 다른 PC의 접속 허용

Windows 방화벽 규칙 생성은 현재 세션에서 액세스 거부로 실패했다. 서버 PC에서 **관리자 PowerShell**로 아래 명령을 한 번 실행해야 한다. 같은 서브넷의 TCP 5184만 허용한다. Wi-Fi 단말 격리가 설정돼 있으면 네트워크 관리자 측에서도 해제가 필요하다.

```powershell
New-NetFirewallRule -DisplayName 'EventQuest recovery demo 5184' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5184 -RemoteAddress LocalSubnet -Profile Any
```

시연 종료 후 규칙을 제거하려면 관리자 PowerShell에서:

```powershell
Remove-NetFirewallRule -DisplayName 'EventQuest recovery demo 5184'
```
