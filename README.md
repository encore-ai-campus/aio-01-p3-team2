# 게임 라이브 이벤트 기획·검수 AutoHealing 시스템

> 가상의 게임사 Okepong Games의 모바일 RPG Opong Heroes를 대상으로, 라이브 이벤트 초안을 만들고 게임 규칙과 수행 가능성을 검수하는 내부 운영용 멀티 에이전트 시스템입니다.

## 프로젝트 소개

Event Planner가 이벤트 초안을 만들고, Balance & Rule Agent와 Critic이 게임 데이터 및 기획 기준에 따라 검수합니다. 문제가 발견되면 Supervisor가 정해진 복구 흐름에 따라 수정·재검증하거나 안전하게 종료합니다. 최종 이벤트 결과는 운영 담당자가 확인하고 승인하는 것을 목표로 합니다.

이 프로젝트는 실제 게임 서버에 이벤트를 배포하는 도구가 아니라, 내부 운영 담당자의 기획과 검수를 돕는 시스템을 대상으로 합니다.

## 현재 상태

**초기 프로젝트 뼈대 단계입니다.** 디렉터리와 일부 설정 파일만 준비되어 있으며 애플리케이션 코드, 컨테이너 실행 설정, CI 워크플로는 아직 구현되지 않았습니다. 현재 상태에서는 서비스를 설치하거나 실행할 수 없습니다.

## 예정 구성

| 구성 | 계획된 역할 |
| --- | --- |
| Frontend | Streamlit 기반 이벤트 입력·결과·운영 화면 |
| Backend | FastAPI API, Supervisor, Agent Workflow, 검증·복구 처리 |
| MCP Server | 게임 규칙·아이템·Mock 게임 데이터 조회 |
| PostgreSQL | Workflow 상태, Trace, 멱등성 및 테스트 결과 저장 |
| Ruff | Python 린트, 포맷, import 정렬 기준 |
| Docker Compose / GitHub Actions | 서비스 통합 실행 및 CI/CD |

## 문서 안내

- [시스템 기획안](docs/last/게임%20라이브%20이벤트%20기획·검수%20AutoHealing%20시스템%203f181f168976802fa10cf20099fe09eb.md): 목표, 사용자 흐름, 시스템 정책 및 배포 구상
- [개발 기획안](docs/last/개발%20기획안%203f181f168976803581dbf5ff867e2125.md): 디렉터리 구조, Agent·데이터 계약, 테스트 및 개발 계획
- [작업 지침](AGENTS.md): Ruff, 문서 작성, 브랜치 관리 원칙
- `docs/team/`: Frontend, Backend 1·2, MCP 담당자의 작업 문서 공간
- `docs/v1/`: 이전 버전의 문서와 검수 기록

## 개발 환경 기준

- Python 대상 버전: 3.11 (현재 Ruff 설정 기준)
- Python 린트 및 포맷: Ruff
- 서비스 프레임워크와 배포 방식은 개발 기획안을 기준으로 구현 예정

Ruff 개발 의존성은 구현 시작 후 다음 명령으로 설치할 수 있습니다.

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
```

## 협업 방식

- `main` 브랜치는 팀장이 관리합니다.
- 팀원은 본인 작업 브랜치에서 작업하고, 완료 후 브랜치 이름·변경 내용·확인 결과를 팀장에게 공유합니다. PR은 사용하지 않습니다.
- 팀별 문서는 `docs/team/<role>/`에 작성하고 공통 기준 문서는 `docs/` 아래 공통 위치에서 관리합니다.
- 비밀값은 저장소에 올리지 않습니다. 실제 환경변수 파일 대신 예시 파일을 사용합니다.
