# IaC·IAM·SSM·Parameter Store 구성 검증

기준: `ikonly`, 2026-10-12. 선행 commit `0b922de`. [구현 계획 24 §16](../기획안설계/24_AWS_배포_CICD_구현계획.md#16-iaciamssmparameter-store-구성-2026-10-12)을 따른다.

## 1. 이번에 구현한 범위

| 영역 | 구현 파일·동작 |
|---|---|
| IaC | `infra/aws/iac.py`에서 `stacks/{network,data,access,application,observability}.yml`과 `ssm/IkonlyDeploy.yml` 생성. JSON 문법의 YAML이며 CloudFormation에서 사용 가능. `--check`로 소스와 생성 파일 일치 검사 |
| 네트워크 | 독립 VPC, public subnet 1개, app subnet 1개, 서로 다른 AZ의 private DB subnet 2개, NAT 1개. 웹 2개만 공개/EIP. SSH 없음. 웹→지정 API→대응 MCP, API/MCP→RDS만 SG 참조로 허용 |
| 데이터 | 암호화 private PostgreSQL 16 계열, gp3 20GB, SSL 강제, deletion protection, 7일 자동 백업, 삭제/교체 시 snapshot. 실제 제공 engine version을 확인해야 plan 가능 |
| 호스트 | 공식 Ubuntu 24.04 amd64 AMI 검증, 6개 EC2/별도 profile, T3 Standard·암호화 디스크·IMDSv2/hop1·컨테이너 metadata 차단. 호스트 bootstrap 완료는 앱 준비 완료가 아님 |
| IAM | 6개 runtime profile, 별도 bootstrap/migration profile. 정확한 Parameter 이름만 읽고 `GetParametersByPath` 없음. bootstrap은 master/role 초기화 값, migration은 owner URL만 읽음 |
| SSM | `IkonlyDeploy`는 release SHA/hash만 받음. 임의 shell 입력 없음. host의 검토된 `deploy-release` helper만 호출. helper는 A6에서 설치하며 현재 호출하면 실패 |
| Parameter Store | 역할별 host loader, root 전용 atomic `runtime.env`/0600, 값 출력 없음. 최초 SecureString import는 gitignored 파일에서 명시 실행. 기존 값을 덮어쓰거나 key를 자동 회전하지 않음 |
| 변경 적용 | read-only preflight→change set 생성→내용 review→검토 hash가 일치할 때만 apply. 계정/region/소유권/template/parameter 대조. apply 결과는 실행 요청이며 완료 판정이 아님 |
| 증거 | 완료 stack의 environment manifest, 생성 실패 중 자원 ID를 남기는 `--partial` snapshot, 실제 EC2/SSM/RDS·IAM 범위 조회 검사 |
| 관찰성 | 14일 로그, SNS email confirmation, CPU/credit/status 및 RDS 저장 공간 경보. 암호화·비공개·버전 관리 S3, backup 7일/evidence 30일 lifecycle |
| CI | 독립 account-free infrastructure workflow와 기존 `quality-core`에 hash lock/cfn-lint/생성 일치 검사. 이 job 실패는 기존 image release gate에서 차단 |

HTTP 80은 인증서 발급·HTTPS redirect용 공개 포트다. HTTPS 443은 운영자 IPv4 CIDR로 제한한다. 인터넷 outbound 80/443은 패키지 설치·AWS·OpenAI·ACME에 필요하며 도메인별 방화벽은 이번 SG 구현 범위가 아니다. DB subnet은 NAT default route가 없다.

## 2. 실제 검증 결과

- Windows 신규 계약 **18 PASS**, 기존 schema/영향 검사 포함 **51 PASS, 3.05초**. 설정 필수값·수동 종료, 네트워크 경계, RDS/TLS 설정, 역할별 IAM, 고정 SSM 문서, 실제 `CloudAccess` proxy 계약, secret override/개행 차단, root/다른 계정 거부, change set 바인딩, 기존 key 보존, 부분 실패 snapshot, 적용 hash/실행 요청 구분, 실제 AWS 조회의 권한 거부 fixture, 큰 요청의 임시 파일 전송/삭제를 검사했다.
- `cfn-lint 1.57.2`: 5개 template **PASS**. 첫 검사에서 AMI parameter의 일반 String type 경고가 나와 `AWS::EC2::Image::Id`로 수정했다.
- `actionlint`: CI/infrastructure workflow **PASS**.
- Linux Python 3.11 최종: IaC hash lock 설치·생성 일치·배포 schema export·cfn-lint·shell 문법·전체 Ruff 통과, core **564 PASS / 1 SKIP, 15.09초**. skip은 기존 PostgreSQL parameter이며 A4의 DB job에서 확인했다. 이전 rehearsal은 561 PASS/1 SKIP였다. Python inventory 72개(core 53, postgres 14, external 3, compatibility 2), JS 8개다. UI/DB/image를 바꾸지 않아 해당 전체 시험은 반복하지 않았다. 새로운 실제 RDS/LLM 수용 결과는 아니다.

마지막 payload 회귀 fixture의 `Path` import 누락을 린트와 시험이 검출해 수정했다. inventory 갱신 시 Windows 기본 cp949 읽기 실패도 UTF-8 명시로 수정했다. 최종 검사에서 해결됐으며 테스트/규칙을 삭제해 우회하지 않았다. A5 rehearsal 컨테이너는 `--rm`으로 제거했고 단계 label로 잔존 없음 확인. 기존 사용자 DB 컨테이너는 변경하지 않았다.

최초 Linux rehearsal은 Windows bind mount의 실행 bit 때문에 기존 Python 파일 215개가 Ruff EXE002로 실패했다. 제품 규칙을 완화하지 않고, 별도 Linux 파일 복사본을 Git의 일반 파일 mode에 맞춰 검사하는 방식으로 변경했다. 소스 체크아웃의 파일 권한이나 기존 사용자 서버를 수정하지 않았다.

## 3. IAM 조사로 확인한 보완

AWS 관리형 [AmazonSSMManagedInstanceCore 정책](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html)은 `ssm:GetParameter`와 `GetParameters`를 모든 자원에 허용한다. 이를 붙이고 좁은 Parameter policy를 추가하는 것만으로는 격리되지 않는다. 따라서 관리형 정책을 붙이지 않고 SSM agent/channel 연결 권한과 정확한 parameter ARN을 별도 정의했다. 실제 agent 기능과 host decrypt는 AWS 단계에서 검증해야 한다.

[SecureString 동적 참조](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-ssm-secure-strings.html)로 RDS master password의 고정 version을 참조한다. [CloudFormation Parameter 자원](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ssm-parameter.html)은 SecureString 생성을 지원하지 않아 별도 import 절차를 사용한다. master 값은 template·plan·명령 인자에 넣지 않는다. 암호화 원문 key는 재배포마다 생성하지 않는다.

## 4. 실제 AWS 적용 전 필요한 사항

첫 AWS 로그인 세션은 만료됐고, 다시 열어 **사용자 승인·IAM user 로그인·STS identity 확인을 완료**했다. 현재 컴퓨터의 공인 IPv4 `/32`와 수동 종료를 사용하기로 확정했다. 실제 값은 Git에서 제외되는 `test-results/aws-a5/config.json`에만 기록했다. 도메인은 아직 없다고 확인했으므로 실제 자원 생성은 보류했다. 이번 작업에서 AWS 자원 생성·Parameter 업로드·ECR push·실제 SSM 명령·유료 LLM 호출을 수행하지 않았다.

실제 계정의 AWS `validate-template`에 **5개 모두 PASS**. 공식 Canonical owner `099720109477`/x86_64/Ubuntu 24.04, `ami-0c713e716602facc2`가 available임을 확인했다. PostgreSQL 16.9~16.15가 제공되며 신규 대상은 계획 검토 시 version을 고정한다. 생성 후보 주요 동작 8개(Create/ExecuteChangeSet, RunInstances, CreateDBInstance, CreateRole, PassRole, PutParameter, CreateRepository)는 현재 IAM user policy simulation에서 allowed였다. 이는 실제 생성 성공이나 quota/조직 정책까지 보장하는 증거가 아니다. `ikonly-demo-*` stack은 현재 없다. 조회 증거는 `test-results/aws-a5/readonly-discovery.json`에 남겼다.

Windows의 32K 명령줄 제한 때문에 긴 template를 wrapper argv로 전달할 때 실패했다. AWS CLI JSON 요청을 owner 전용 임시 파일로 전달하고 finally에서 제거하도록 수정한 뒤 5개 모두 실제 AWS 검증에 성공했다. secret import도 값이 argv에 들어가지 않는 별도 파일 전달을 사용한다.

OIDC trust는 `ik-e/multi-agent-autohealing`, audience `sts.amazonaws.com`, environment `ikonly-demo`에 고정한 후보이며 기본 생성 조건은 false다. GitHub 조회에서 저장소는 PRIVATE/default main, 지정 environment API는 404였다. environment가 없거나 조회 권한이 부족할 수 있어 보호 설정이 확인됐다고 판단하지 않는다. AWS 계정의 기존 GitHub OIDC provider는 발견했지만 변경하지 않았다. 실제 subject 형식·reviewer/branch protection을 확인하기 전 CLI가 EnableGithubRoles=true를 거부한다. 새 workflow는 인증 없는 검사만 수행한다. GitHub infrastructure 후보 role에는 change set planning 권한만 있고 execute/PassRole은 없다. 실제 보호 설정과 execution identity를 확인한 뒤 계정 내 초기 bootstrap 및 원격 plan/apply 연결을 수용해야 한다. [AWS OIDC 지침](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)을 따른다.

실제 apply 후 `verify_infrastructure`는 EC2 소유권/IMDS·private host·정상 runtime profile 복귀·SSM online·private encrypted RDS와 IAM 시뮬레이터의 Parameter 허용/거부를 검사한다. IAM 시뮬레이션은 host의 실제 decrypt 성공을 대체하지 않는다. 같은 서버의 2 DB/6 DB role·TLS·초기 데이터 적용은 A2 도구를 A6 배포 절차에 연결한 뒤 실제 RDS에서 확인한다.

메모리/디스크 agent 설치·앱/컨테이너 로그 전송·복구율/업무 지표·알림 수신·백업/복원·정리는 아직 미구현/미수용 영역이다. 사용자 결정에 따라 현재 `termination_mode=manual`, `stop_after=null`이며 자동 종료 스케줄이 아니다. 기존 scheduled manifest의 시간대 검증은 호환 유지한다. RDS snapshot과 SecureString key의 기한별 정리는 A8에서 수행해야 한다. S3 lifecycle만으로 snapshot/Parameter/ECR가 삭제되지 않는다. 실행 RDS의 `DeletionPolicy=Snapshot`은 snapshot을 남기기 위한 것이므로 manifest에서 실행 DB 자체를 영구 Retain 대상으로 분류하지 않는다.

## 5. 실행 절차

저장소 루트에서 소스 일치 확인:

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.iac --check
```

실제 환경 설정·parameter 입력·기록은 Git에서 제외되는 `test-results/aws-a5/`에 둔다. 환경 설정은 `infra/aws/config/demo.example.json`의 미정 값을 확인한 실제 값으로 채운 파일이다. 비밀값은 이 설정에 포함하지 않는다. `stage-parameters.json`은 해당 template parameter 이름/실제 값만 포함한다.

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.infrastructure preflight --config test-results/aws-a5/config.json --output test-results/aws-a5/preflight.json
backend/.venv/Scripts/python.exe -m infra.aws.infrastructure plan --config test-results/aws-a5/config.json --stage network --parameters test-results/aws-a5/stage-parameters.json --output test-results/aws-a5/network-plan.json
backend/.venv/Scripts/python.exe -m infra.aws.infrastructure review --config test-results/aws-a5/config.json --record test-results/aws-a5/network-plan.json --output test-results/aws-a5/network-reviewed.json
```

plan은 CloudFormation change set을 생성한다. 아직 처리 중이면 review가 거부하므로 AWS 상태를 확인하고 다시 review한다. 변경 목록·비용·삭제/교체 영향을 확인한 뒤 `apply --record <reviewed.json> --approve-sha256 <changes_sha256> --output <apply.json>`를 명시 실행한다. source/template/config가 바뀌면 다시 plan한다. stack 완료를 실제 AWS에서 확인한 뒤 다음 stack의 ID parameter를 전달한다.

권장 순서는 **network→access→observability 초기→data→application→observability 호스트 ID 보충**이다. data 전에는 master SecureString을 최초 import하고 metadata version을 확인한다. application에는 완료된 network/access의 subnet/SG/profile 값과 공식 AMI ID를 전달한다. cross-stack export를 사용하지 않으므로 cleanup에서 실제 참조/의존성을 별도 대조해야 한다.

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.put_parameters --config test-results/aws-a5/config.json --values-file test-results/aws-a5/secure-values.json
```

위 명령은 이름/형식 검증만 한다. 업로드는 `--apply`를 추가해야 한다. 키는 `main/runtime-database-url`처럼 prefix 뒤 suffix이며 값은 실제 secret 문자열이다. 처음 만들 값만 선택하고, 파일 접근 ACL을 본인/필요한 관리자로 제한한다. 값은 argv 대신 임시 owner 파일로 AWS CLI에 전달하고 파일은 finally에서 지운다. 부분 실패 시 이미 만들어진 parameter를 보존하고 metadata로 누락분을 확인한다.

Linux 호스트의 `load-parameters.sh --config <host-config.json>`는 저장소 모듈이 설치된 root 작업 위치에서 실행한다. host config에는 `environment`의 비밀값 없는 설정과 API의 `proxy_peers`만 허용한다. 실제 env 이름은 [24 §4/§14](../기획안설계/24_AWS_배포_CICD_구현계획.md#4-실행설정권한-계약)와 production entry를 따른다. API peer 키는 `plan-web`/`game-web`이며, fault는 `plan-web`만 받는다. `/etc/eventquest/service-role`에서 자기 역할을 읽어 필요한 SecureString만 가져온다. A6에서 host config 생성·module 설치·배포 helper와 연결해야 한다.

완료 후 `python -m infra.aws.export_environment --config <config> --output <manifest>`와 `python -m infra.aws.verify_infrastructure --config <config> --environment-manifest <manifest> --output <result>`를 사용한다. 생성 실패 중에는 export에 `--partial`을 붙여 불완전 snapshot을 남긴다. 부분 snapshot은 정상 environment manifest나 삭제 승인서가 아니다. CloudFormation 밖에서 만든 parameter의 소유권/보존/정리 목록은 A8에서 추가 대조해야 한다.
