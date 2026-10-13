# AWS 배포 운영 실행 가이드

2026-10-13 최신 AWS 상태: **ACTIVE**. main source `18bd89e`를 Actions로 첫 배포했고, 실제 LLM 정상 흐름 17 PASS·RDS 암호화/SSL·Edge 3 PASS를 확인했다. [main 첫 배포·접속·증거](../검증산출물/2026-10-13_main_AWS_첫배포.md). 아래 ABSENT·INACTIVE 기록은 이번 배포 전 이력이다.

2026-10-13 현행 통합·CI/CD·OIDC 기준은 `main`이다. 기존 `ikonly` source의 시험은 당시 증거로 보존한다. [main 전환·실행 결과](../검증산출물/2026-10-13_main_CICD_전환과_병합.md).

2026-10-13 최신 운영은 [Actions 운영 27](27_Actions_첫배포_초기화_운영.md)을 따른다. 첫 배포/초기화 전체 로컬 실행은 기존 비상 경로이며, 현재 AWS 앱은 **ABSENT / 잔존 17개 항목 0**이다. 아래 ACTIVE 기록은 이전 첫 배포 수용 시점의 기록이다. [실제 정리 결과](../검증산출물/2026-10-13_Actions_수명주기_전환과_AWS_초기화.md)를 확인한다.

## 기준과 현재 상태

2026-10-13 통합 후 현행 source의 CI/CD 마무리와 원격 실행 상태는 [최신 검증 기록](../검증산출물/2026-10-13_현행코드_CICD_마무리.md)을 따른다. 아래 이전 AWS 수용 이력과 현재 source의 CI 검증을 구분한다. 첫 배포에 사용할 artifact는 해당 source의 필수 검사와 이미지 gate를 통과한 동일 run에서 가져온다.

상위 계약은 [수명주기 25](25_AWS_배포수명주기_정리.md), 구현 순서는 [계획 24](24_AWS_배포_CICD_구현계획.md)다. 사용자는 **배포 인증 연결 유지**, **첫 배포는 로컬 키 공급, 이후 자동 업데이트**를 확정했다. 실제 첫 배포·이미지 rollback·게임 웹 선택 업데이트·GitHub 전체 업데이트·전체 초기화와 빈 환경 반복 초기화가 통과했다. 2026-10-13 현행 source 첫 배포 수용 후 앱은 **ACTIVE**다. 공개 주소·현재 허용 IP·새 CA/로그인 파일·검증 범위는 [최신 첫 배포 결과](../검증산출물/2026-10-13_AWS_현행코드_첫배포_검증.md)를 따른다. 아래 빈 환경 CD 기록은 재배포 전 이력이다.

| 실행 | 진입점 | 동작 |
|---|---|---|
| 첫 배포 | 로컬 `operations initial` | 성공 CI tar 검증→5 stack 생성→키 공급→DB 초기화→6 서비스 연결 수용 |
| 선택 업데이트 | main push / `update` | 각 서비스 실제 source부터 변경 계산, 필요한 역할만 교체 |
| 전체 업데이트 | workflow dispatch `full` | 기존 DB/인프라 유지, 여섯 역할 선택 |
| 이미지 복구 | 자동 rollback / `recover` | 이전 서비스별 immutable 배치 복원 후 전체 연결 검사 |
| 실패 재시도 | 로컬 `retry` | DB migration 시작 후에는 실패한 정확한 source/artifact만 재시도 |
| 전체 초기화 | 로컬 `reset` | 증거를 사용자 PC에 보관·검증 후 앱 자원 전체 삭제 |

첫 배포/초기화는 키와 시험 증거가 사용자 PC에 있어야 하므로 로컬 실행한다. GitHub runner에 OpenAI 키를 공급하거나 원시 로그·시험 저널을 업로드하지 않는다. 인증서 제거는 사용자 보류 상태다.

## 인증 및 CI 연결

`bootstrap_oidc.py`는 기존 공유 GitHub OIDC provider를 재사용한다. `ikonly-control-controller`, `ikonly-control-cloudformation`만 관리하며 기존 계정 provider를 초기화하지 않는다. 저장소 실제 immutable subject ID를 API에서 확인해 **main 브랜치만** 신뢰한다. [GitHub OIDC 문서](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)의 subject 규칙을 따른다. CloudFormation 서비스 역할은 CFN principal과 정확한 PassRole을 사용한다. [AWS 서비스 역할 기준](https://docs.aws.amazon.com/prescriptive-guidance/latest/least-privilege-cloudformation/service-roles-for-cloudformation.html).

```powershell
$env:PYTHONPATH='.'
backend/.venv/Scripts/python.exe -m infra.aws.bootstrap_oidc --config test-results/aws-a5/config.json --output test-results/aws-lifecycle/oidc-plan.json --apply
```

생성한 `test-results/aws-lifecycle/operation-config.json`에는 CFN 역할 ARN이 포함된다. GitHub 변수 `IKONLY_DEPLOY_ROLE_ARN`, `IKONLY_ACCOUNT_ID`, `IKONLY_CONFIG`에는 비밀값이 없다. `IKONLY_CD_ENABLED=true`는 실제 OIDC 인증 성공 후 활성화했다. legacy config의 `auto_deploy_enabled`와 이 변수는 서로 다른 게이트다.

```powershell
gh workflow run ikonly-ci.yml --repo ik-e/multi-agent-autohealing --ref main -f operation=oidc-check
```

필수 CI·이미지 승인 검사가 모두 통과한 같은 run의 immutable 이미지 tar만 배포한다. 현재 deploy job의 run이 아직 진행 중이어도 선행 CI job/gate 성공을 모두 확인한다. 실패한 CI 이미지는 배포하지 않는다. 전체 workflow 실패가 배포 job에서만 발생했다면 성공한 선행 CI artifact를 복구 재시도에 사용할 수 있다.

CI의 pipeline 대기열과 `recover/oidc-check` 운영 대기열은 분리한다. GitHub의 한 concurrency group당 pending slot 제한 때문에 운영 점검이 대기 중인 push를 교체하지 않도록 한 것이다. 실제 deploy/operations job은 공통 `ikonly-demo-lifecycle` concurrency와 AWS 작업 lock을 계속 공유한다. 같은 pipeline group의 중간 pending push가 최신 push로 교체될 수 있으므로 취소된 run은 성공 artifact로 사용하지 않고 최종 SHA의 완료 run을 확인한다.

## 현재 검증된 첫 배포 후보

2026-10-13 완료 run은 `38308564165`, source는 `a78f68f3493ffc6f4d4dfc578d635c2aecf7ba05`, attempt는 `1`이다. 필수 CI·이미지·승인 게이트가 모두 성공했고 자동 CD는 빈 환경을 확인해 INACTIVE로 종료했다. 앱 재생성은 하지 않았다. 이번 기능/검사 source 뒤의 문서만 바뀐 커밋은 이 검증 source를 새 이미지로 표현하지 않는다.

첫 배포 후보를 받으려면 아래 두 artifact를 같은 directory에 다운로드한다. 기존 큰 artifact 이어받기 방법은 아래 절을 따른다. artifact 만료 시 새 CI run을 사용한다.

```powershell
gh run download 38308564165 --repo ik-e/multi-agent-autohealing --name images-a78f68f3493ffc6f4d4dfc578d635c2aecf7ba05-1 --dir test-results/aws-current-candidate
gh run download 38308564165 --repo ik-e/multi-agent-autohealing --name verified-a78f68f3493ffc6f4d4dfc578d635c2aecf7ba05-1 --dir test-results/aws-current-candidate
```

다운로드 후 아래 `initial` 명령에 `--directory test-results/aws-current-candidate --run-id 38308564165`를 사용한다. 실제 생성에는 로컬 AWS 인증, 기존 operation config, `backend/.env`, 승인 정책 파일과 hash가 필요하다. config의 현재 접근 허용 IPv4 /32도 확인한다. CI 승인 자체는 첫 배포의 사용자 승인이나 정책 승인을 대신하지 않는다. 현재 source의 새 AWS 서비스 수용은 첫 배포 후 별도로 기록한다.

## 로컬 실행 명령

`<...>`는 실제 값으로 바꾼다. artifact directory는 성공 run의 CI/image/verified 파일이 모인 경로다. 키 원문·비밀번호·개인 `.env`·인증서 파일은 커밋하지 않는다.

```powershell
# 상태 조회
backend/.venv/Scripts/python.exe -m infra.aws.operations status --config test-results/aws-lifecycle/operation-config.json --output test-results/aws-lifecycle/status
# 첫 배포: 정책은 검증·활성화할 실제 문서와 그 SHA256을 지정
backend/.venv/Scripts/python.exe -m infra.aws.operations initial --config test-results/aws-lifecycle/operation-config.json --directory <CI-artifact-directory> --run-id <run-id> --env-file backend/.env --policy-file <policy.json> --approved-policy-sha256 <sha256> --output test-results/aws-lifecycle/initial
# 선택 / 전체 업데이트
backend/.venv/Scripts/python.exe -m infra.aws.operations update --config test-results/aws-lifecycle/operation-config.json --directory <CI-artifact-directory> --run-id <run-id> --output test-results/aws-lifecycle/update
backend/.venv/Scripts/python.exe -m infra.aws.operations full --config test-results/aws-lifecycle/operation-config.json --directory <CI-artifact-directory> --run-id <run-id> --output test-results/aws-lifecycle/full
# 이미지 복구 / 정확한 실패 소스 재시도
backend/.venv/Scripts/python.exe -m infra.aws.operations recover --config test-results/aws-lifecycle/operation-config.json --output test-results/aws-lifecycle/recover
backend/.venv/Scripts/python.exe -m infra.aws.operations retry --config test-results/aws-lifecycle/operation-config.json --directory <failed-CI-artifact-directory> --run-id <failed-run-id> --output test-results/aws-lifecycle/retry
# 사용자 PC에 전체 증거를 보관하고 초기화
backend/.venv/Scripts/python.exe -m infra.aws.operations reset --config test-results/aws-lifecycle/operation-config.json --confirm-environment ikonly-demo --output test-results/aws-lifecycle/reset
```

미분류 코드/IaC 영향은 `REVIEW_REQUIRED`로 멈추고 `impact-review.json`을 기록한다. 검토한 hash만 `--approve-impact-sha256 <sha256>`으로 지정한다. 자동 CD가 불명확한 변경을 승인하지 않는다. 인프라 변경은 별도 change set 검토가 필요하다.

## 상태·멱등성·실패 의미

- 앱 accepted state는 `/multi-agent-autohealing/ikonly-demo/deployment/accepted`다. 여섯 역할 각각 실제 source SHA·image digest·deployment ID를 기록한다. 부분 업데이트에서 유지한 역할을 새 source로 덮어쓰지 않는다.
- 작업 lock은 `/multi-agent-autohealing/control/ikonly-demo/lock`이다. 원자적 생성과 소유 token 확인 삭제를 사용한다. GitHub concurrency와 함께 로컬/원격 동시 작업을 차단한다.
- ABSENT 환경의 자동 업데이트는 `INACTIVE`로 끝나며 자원을 생성하지 않는다. 동일 이미지 이미 수용한 요청은 no-op이다.
- 선택 역할을 immutable 파일에 준비하고, 점검/drain 후 MCP→API→web으로 교체한다. 미선택 역할을 포함해 image revision/health·DB/MCP readiness·TLS/인증·게임/시험실 연결을 검사하고 accepted state를 갱신한다.
- 이미지 교체 실패는 이전 서비스별 배치를 되돌리고 수용 검사를 한다. 복구 실패 시 `RECOVERY_REQUIRED`와 점검 상태를 유지한다.
- DB/catalog migration은 암호화 RDS snapshot 생성 후 실행한다. migration이 시작되면 이전 이미지만 자동 복구하지 않는다. 정확한 실패 source로 재시도하거나 운영자가 DB/코드 호환성을 검토한다. **snapshot 복원을 자동 실행하는 기능은 구현하지 않았다.**
- 첫 배포 실패 자동 정리는 이번 작업 token의 빈 환경 시작 marker가 있을 때만 수행한다. 다른 환경 또는 이미 수용된 성공 환경을 지우지 않는다.
- 오래된 lock은 자동 탈취하지 않는다. 기록 token과 GitHub 소유 run 종료를 확인해 `unlock --confirm-environment ikonly-demo --lock-token <token>`으로 해제한다. 로컬 소유 프로세스 종료 여부는 운영자가 확인한다.

## 첫 배포 키 임시 보관과 실패 정리

상태: **아래는 보완 전 분석 기록이다.** 세대 등록·hash 확인·run 소유권·실패 정리·만료/잔여 sweep을 구현하고 관련 단위 검사 52개를 통과했다. 실제 확인과 남은 한계는 [SSM 보완 검증](../검증산출물/2026-10-13_SSM_임시공급_세대관리_보완.md), 현재 운영 계약은 [Actions 운영 27](27_Actions_첫배포_초기화_운영.md)을 따른다.

분석 당시 `lifecycle_dispatch.stage()`는 첫 배포 설정을 `SecureString` chunk와 만료 시각으로 SSM Parameter Store에 기록했다. 만료 시각은 소비 시 검사할 뿐 SSM 파라미터를 자동 삭제하지 않았다. GitHub Actions가 dispatch에 도달하기 전 실패하거나, dispatch의 payload 검증 단계에서 중단되면 `bootstrap/*` 값이 남을 수 있었다. 재등록 때 chunk 수가 줄면 이전 등록의 초과 chunk도 남을 수 있었다.

당시 권장한 보완 계약은 다음과 같다.

1. **세대 단위로 등록한다.** 등록마다 임의의 generation ID를 만들고 해당 ID 아래에 chunk를 쓴다. 모든 chunk 저장 후 개수·내용 hash를 확인한 다음 활성 generation 포인터를 갱신한다. 새 등록이 실패하면 기존 활성 세대는 그대로 둔다.
2. **정확한 세대만 소비·삭제한다.** 첫 배포는 활성 포인터가 가리키는 세대의 개수·만료·config hash·payload hash를 확인한다. 성공·실패를 포함해 dispatch 종료 때 해당 세대의 chunk와 포인터를 삭제한다. chunk count만으로 삭제 범위를 추측하지 않는다.
3. **조기 실패와 재등록 잔여물을 회수한다.** AWS 인증 뒤 실행되는 workflow 정리 단계는 `always()`로 실행하고, 해당 run이 소유한 generation만 삭제한다. workflow가 AWS 인증 전에 끝나거나 정리가 실패한 경우를 위해 다음 로컬 stage는 작업 lock을 확인한 뒤 만료 세대와 미활성 잔여 세대를 정리한다. 활성 배포가 소비 중인 세대는 정리하지 않는다.
4. **실패를 드러내고 비밀은 출력하지 않는다.** 정리 성공 여부와 세대 ID·남은 파라미터 개수만 안전한 결과 metadata에 기록한다. 키·payload 원문은 로그나 artifact metadata에 기록하지 않는다. 정리 실패는 성공으로 처리하지 않고 재시도 가능한 운영 상태를 남긴다.

완료 검증은 등록 도중 실패, dispatch 전 workflow 실패, payload 검증 실패, chunk 수가 감소한 재등록, initial 성공·실패 후 SSM 잔여 조회를 포함한다. 모든 경우에 활성 generation 이외의 만료/미활성 비밀 chunk가 남지 않고, 실행 중인 활성 세대가 조기 정리되지 않는지 확인한다.

## 초기화 범위와 검증

시험 저널, S3 모든 object version/delete marker, CloudWatch/RDS 로그를 로컬로 보관한다. 객체 크기/SHA 확인 실패 시 삭제에 진입하지 않는다. observability→application→data→access→network 역순으로 삭제한 뒤 manual snapshot/ECR/log/S3/앱 Parameter를 제거한다. 마지막 조회에서 EC2/NAT/VPC/EIP/volume/RDS/snapshot/automated backup/Parameter/ECR/log/S3와 runtime IAM/profile/SSM document/alarm/SNS 잔존을 확인한다.

제어 IAM 두 역할과 공유 OIDC는 유지한다. 앱 OpenAI Parameter는 삭제되므로 다음 첫 배포는 다시 로컬 키 공급이 필요하다. 이미 발생한 요금과 종료된 인스턴스/CFN 이력은 잔존 실행 자원과 다르다. 실제 새 경로 시험 결과는 [구현 검증 기록](../검증산출물/2026-10-12_AWS_배포수명주기_구현검증.md)에 누적한다.

첫 배포 수용 후 두 공개 root CA는 로컬 출력 폴더의 `plan-web-root.crt`, `game-web-root.crt`로 내보낸다. Windows 신뢰 등록은 자동 수행하지 않는다. 개인 비밀번호는 로컬 `web-login.txt`에만 저장한다.

## 큰 CI 이미지 artifact 다운로드

`gh run download`가 대용량 전송 중 연결 종료로 실패하면 아래 명령으로 이어받는다. API에서 확인한 이미지 artifact ID를 사용하며, 같은 output 경로로 재실행하면 완료 범위를 재사용한다. GitHub가 제공하는 ZIP SHA256 검사를 통과한 후 추출한다. 작은 verified artifact는 `gh run download --name verified-<sha>-<attempt> --dir <같은-directory>`로 받는다. 두 파일 세트가 모두 있어야 첫 배포 검증을 통과한다.

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.download_artifact --artifact-id <id> --output <CI-artifact-directory>
```

## 전체 초기화와 snapshot

전체 초기화는 사용자 승인에 따른 DB 전체 삭제다. 신규 data stack의 `DeletionPolicy=Delete`로 초기화 때 최종 snapshot을 새로 만들었다가 바로 삭제하는 대기를 줄인다. 기존 manual snapshot과 automated backup도 잔존 검사·삭제 대상으로 관리한다. migration 전 암호화 snapshot 및 `UpdateReplacePolicy=Snapshot`은 유지한다. 이미 삭제 진행 중인 구 stack은 기존 Snapshot 정책으로 처리되며 중간에 정책을 바꾸지 않는다. reset 진입 전 로컬 시험 증거/로그/저널/S3 보관은 동일하게 수행한다.

수동 workflow update/full은 `impact_approval_sha256` 입력으로 기록된 정확한 검토 hash를 전달할 수 있다. 64자 hex 검사를 거치고 실제 배포 baseline/후보 diff의 hash와 일치할 때만 승인한다. push의 기본 입력은 빈 값이므로 수동 검토를 우회하지 않는다.

## 서비스 시작 전 첫 배포 이어받기

코디네이터가 중단됐을 때 원래 프로세스가 종료됐음을 확인하고 원래 token으로 lock을 해제한다. `initial`의 기존 인수에 `--resume-from <원래-출력-directory>`를 추가하고 새 출력 directory를 사용한다. 원래 빈 baseline marker/token·config hash·CREATE 검토 기록·실제 stack ID/role/template/parameters가 일치해야 한다. 앱 accepted state가 없고 앱 Parameter가 원래 master 하나뿐인 서비스 시작 전 단계만 허용한다. master 값/버전을 새로 쓰지 않고 기존 네트워크·IAM·RDS·EC2를 재사용한다. 앱 키 공급/DB bootstrap 이후에는 이 명령으로 이어받지 않고 기록된 상태에 따른 recover/reset을 사용한다.

API/MCP는 private IP에 publish하므로 수용 검사도 실제 private IP를 사용한다. 웹 TLS 검사는 loopback 연결과 실제 공개 hostname 검증을 사용한다.

첫 배포는 공개 CA export 후 실제 비밀번호로 두 HTTPS 화면·게임 API·시험실 catalog를 조회하고 나서 점검 해제/accepted state를 기록한다. stdin 비밀 입력은 bytes로 전달해 Windows CRLF 자동 변환을 피한다. 비밀번호/hash를 argv나 로그에 출력하지 않는다.

수동 선택 업데이트는 `update --service game-web`처럼 역할을 지정할 수 있다(반복 지정 가능). API/MCP는 main/fault 짝을 자동 포함한다. 변경 영향과 schema/migration/catalog 계약이 요구하는 역할을 빠뜨리는 선택은 거부한다. 미분류·인프라 변경의 검토 hash 승인 조건도 유지한다. 자동 push는 기존 변경 영향 계산을 사용한다. 게임 웹의 공개 경로는 `/game-api/*`, 기획안 웹의 일반/시험 경로는 `/api/*`, `/api/test/*`다.

초기화는 quiesce/drain 완료 후 `RESETTING`을 기록해 자동 업데이트를 중단한다. 삭제 중 실패해 재실행할 때는 원래 영구 증거 output directory를 그대로 사용한다. 완료된 quiesce를 확인해 이미 종료된 API host에 다시 명령을 보내지 않는다. 새로운 output으로 증거를 잃는 재시도는 거부한다.
