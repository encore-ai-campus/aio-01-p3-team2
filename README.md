# 게임 라이브 이벤트 기획·검수 AutoHealing 시스템

> 가상의 게임사 Okepong Games의 모바일 RPG Opong Heroes를 대상으로, 라이브 이벤트 초안을 만들고 게임 규칙과 수행 가능성을 검수하는 내부 운영용 멀티 에이전트 시스템입니다.

## 프로젝트 소개

Event Planner가 이벤트 초안을 만들고, Balance & Rule Agent와 Critic이 게임 데이터 및 기획 기준에 따라 검수합니다. 문제가 발견되면 Supervisor가 정해진 복구 흐름에 따라 수정·재검증하거나 안전하게 종료합니다. 최종 이벤트 결과는 운영 담당자가 확인하고 승인하는 것을 목표로 합니다.

이 프로젝트는 실제 게임 서버에 이벤트를 배포하는 도구가 아니라, 내부 운영 담당자의 기획과 검수를 돕는 시스템을 대상으로 합니다.

## 현재 상태

Frontend 화면과 Dockerfile, Backend A API, Backend B Workflow, MCP 서버가 저장소에 구현되어 있습니다. `backend-test`에서 MCP 서버와 백엔드 `MCPClient`의 Streamable HTTP 직접 연결을 검증했습니다. 다만 현재 API Workflow는 Mock 전송을 사용하므로 실제 MCP 서버와 OpenAI 모델을 거치는 전체 흐름은 아직 검증되지 않았습니다. 자세한 결과는 [Backend A/B 통합 및 MCP 연결 시험](docs/team/backend-1/backend_ab_integration_test.md)을 참고하세요.

## 예정 구성

| 구성 | 계획된 역할 |
| --- | --- |
| Frontend | Streamlit 기반 이벤트 입력·결과·운영 화면 |
| Backend | FastAPI API, Supervisor, Agent Workflow, 검증·복구 처리 |
| MCP Server | 게임 규칙·아이템·Mock 게임 데이터 조회 |
| PostgreSQL | Workflow 상태, Trace, 멱등성 및 테스트 결과 저장 |
| Ruff | Python 린트, 포맷, import 정렬 기준 |
| Docker Compose / GitHub Actions | 서비스 통합 실행 및 CI/CD |

## `yejin` 프론트엔드 AWS 배포

- `infra/frontend-yejin.yml`: ECR, SSM용 IAM Role, Security Group, 프론트엔드 EC2를 CloudFormation으로 관리한다.
- `.github/workflows/frontend-infrastructure.yml`: 수동으로 인프라를 생성·업데이트하거나 `destroy=true`로 프로젝트 Stack을 삭제한다.
- `.github/workflows/frontend-deploy.yml`: `yejin` 브랜치의 `frontend/**` 변경을 감지해 테스트, Docker 이미지 빌드, ECR Push, SSM 배포, Health Check를 실행한다.

GitHub Environment `yejin-frontend`에는 `AWS_REGION`, `AWS_ACCOUNT_ID`, `AWS_ROLE_ARN`, `CLOUDFORMATION_ROLE_ARN`, `VPC_ID`, `SUBNET_ID`, `FRONTEND_AMI_ID`, `ALLOWED_FRONTEND_CIDR` 변수가 필요하다. `FRONTEND_ENV_FILE`은 GitHub Secret으로 저장한다.

처음에는 인프라 workflow에서 `apply=true`로 실행하고, 이후 `yejin` 브랜치에 프론트엔드 변경을 Push한다. 테스트가 끝난 뒤에는 같은 인프라 workflow에서 `destroy=true`를 선택해 해당 Stack의 EC2, ECR, IAM, Security Group을 삭제한다.

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
