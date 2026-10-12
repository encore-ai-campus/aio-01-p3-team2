# AWS 첫 배포·업데이트·전체 초기화

## 1. 확정한 동작

2026-10-12 사용자는 시험 증거를 로컬에 남기고 AWS 보존 자원도 모두 삭제해 배포 전 상태로 돌아가도록 요청했다. 또한 이미 배포된 환경을 업데이트하고 각 시스템별로 업데이트할 수 있는 구조를 요청했다. [배포 설계 23](23_AWS_배포_CICD_계획.md), [구현 계획 24](24_AWS_배포_CICD_구현계획.md)의 최신 수명주기 기준은 이 문서다.

| 동작 | 자원·데이터 처리 | 완료 기준 |
|---|---|---|
| 첫 배포 | 5개 stack·6개 서비스·DB/초기 데이터·새 Parameter 생성 | 전체 연결 수용, 배포된 버전 기록 |
| 전체 업데이트 | EC2/VPC/RDS/EIP·DB 데이터를 유지하고 승인 이미지 교체 | 여섯 서비스 및 연결 수용 후 성공 버전 갱신 |
| 시스템별 업데이트 | 변경 영향에 포함된 역할만 교체, 나머지 digest/source 유지 | 선택 역할 및 영향 연결 수용, 미선택 역할 변경 없음 |
| 전체 초기화 | 증거를 로컬에 내려받아 검증한 뒤 실행·저장 자원 모두 삭제 | 이번 환경의 활성·과금 저장 자원·동일 이름 생성 충돌 없음 |

업데이트는 DB 초기화나 인프라 전체 삭제를 수행하지 않는다. 전체 초기화 후 다음 배포는 새로운 환경 생성이며 이전 게임/기획안 데이터를 자동 복원하지 않는다. 이미 발생한 요금·CloudFormation/CloudTrail 이력과 종료된 EC2/NAT 이력은 삭제 완료와 별개다. 기존 계정의 공유 IAM/KMS 기본 키·다른 환경은 변경하지 않는다. 로컬 DB·환경 파일·Git·증거는 유지한다. Windows 인증서 제거는 사용자 보류 상태다.

## 2. 현재 구현 및 실행 사실

| 항목 | 상태 |
|---|---|
| 첫 배포 | 실제 AWS 배포·Edge 시험 수용 완료 이력 있음 |
| 이번 환경 전체 초기화 | 실행 자원에 이어 보존 자원 21개 삭제. 최종 조회 결과는 [정리 결과](../검증산출물/2026-10-12_AWS_정리결과.md) 참조 |
| CloudFormation CREATE/UPDATE 구분 | `infrastructure.py`가 소유 stack 존재 여부로 change set 유형 선택 |
| 변경 영향 계산 | `deployment_impact.py`와 `change_rules.json`에 역할별 규칙 있음 |
| 선택 업데이트 release 구성 | `release_plan.py` 추가. 서비스별 실제 source 기준 영향 계산·미선택 버전 보존 |
| host 이미지 교체 | `host_release.py`에 digest/revision 확인·Compose up·health 검사 있음 |
| 전체 fleet 업데이트 자동 실행·수용 | 코드 연결 완료: immutable 배치→점검/drain→선택 SSM→전체 연결 수용→baseline 기록. 실제 first-deploy/rollback/GitHub full 수용 PASS |
| ikonly push → 자동 CD | workflow/IAM/OIDC 실제 인증 PASS, CD 활성화. ABSENT push는 INACTIVE로 자원 재생성 없음 |
| 전체 초기화 | 로컬 operations reset 연결: 증거 다운로드·SHA 확인→역순 stack 삭제→보존 자원 삭제→잔존 검사. 실제 full/empty 반복 reset PASS |

## 3. 시스템별 업데이트 계약

역할은 `plan-web`, `game-web`, `main-api`, `fault-api`, `main-mcp`, `fault-mcp`다. 공용 이미지가 두 개여도 실행 중인 컨테이너의 교체 대상은 독립적으로 선택할 수 있다.

- 기획안 화면만 변경: `plan-web`.
- 가상 게임 화면만 변경: `game-web`.
- 일반 backend 변경: `main-api` + `fault-api`.
- MCP 진입점 변경: `main-mcp` + `fault-mcp`.
- MCP 도구·공통 계약·데이터·DB migration 변경: 규칙에 따른 연결 서비스 포함. schema/migration/catalog checksum이 바뀌면 전체 서비스 선택.
- 문서만 변경: 서비스 배포 없음.
- 미분류 코드·IaC 변경: 자동 실행 차단 및 별도 검토. IaC는 UPDATE change set의 replacement/deletion 영향도 검토.

main/fault API 쌍과 MCP 쌍은 동일 코드 digest를 유지한다. 격리 시험 환경이 운영과 다른 코드를 검사하는 상태를 피하기 위한 기존 계약이다. DB는 일반 서비스 이미지 업데이트 대상으로 교체하지 않고 버전 관리 migration으로 갱신한다. 같은 이미지에 새 코드가 포함됐다는 이유만으로 모든 host를 재시작하지 않는다.

부분 배포 이후 역할별 source가 다를 수 있다. 따라서 다음 업데이트는 단일 마지막 Git SHA만으로 diff하지 않고 **각 역할이 실제 사용하는 source SHA부터 후보 SHA까지** 계산한다. 실패한 배포·미선택 서비스의 버전을 새 SHA로 덮어쓰지 않는다. `--full`은 모든 역할을 선택한다.

### 계획 생성 명령

검증된 CI tar를 `publish_release.py`로 게시해 얻은 후보 manifest를 입력으로 사용한다. 아래 명령은 AWS를 변경하지 않고 선택 대상 및 여섯 역할의 최종 release를 계산한다.

```powershell
$env:PYTHONPATH='.'
# 첫 배포
backend/.venv/Scripts/python.exe -m infra.aws.release_plan --candidate <candidate.json> --output <plan.json>
# 시스템별 변경 영향에 따른 업데이트
backend/.venv/Scripts/python.exe -m infra.aws.release_plan --candidate <candidate.json> --previous <accepted-release.json> --output <plan.json>
# 전체 업데이트
backend/.venv/Scripts/python.exe -m infra.aws.release_plan --candidate <candidate.json> --previous <accepted-release.json> --full --output <plan.json>
```

현재 출력은 `deployment_authorized=false`다. 후보 manifest의 타입 검증은 CI 출처 검증이나 AWS 배포 승인 자체가 아니며, 앞선 게시 단계의 provenance/필수 CI 성공 검사를 유지해야 한다. 실제 업데이트용 후보는 배포 소스 및 필요한 구버전 baseline이 Git에 존재해야 한다.

## 4. 후속 자동화 구현 순서

1. `infra/aws/operations.py`에서 initial/update/reset 명령·계정/소유 검증·environment 단위 잠금·검토 hash·진행 상태 저장을 통합한다. initial은 같은 이름의 잔존 자원이 있으면 덮어쓰지 않고 중단한다.
2. `infra/aws/fleet_release.py`에서 release plan 선택 역할만 staging/SSM 실행한다. API 변경·migration은 점검/drain 이후 진행하고, MCP → API → 웹 순서와 실제 영향 연결 검사를 사용한다. 웹만 변경할 때는 DB 재초기화와 전역 점검을 하지 않는다. 성공한 fleet 수용 후에만 accepted manifest를 원자적으로 갱신한다.
3. migration은 기존 owner 역할로 적용하고 DB 데이터를 보존한다. 하위 호환 migration·실패 복구 계약을 먼저 확인한다. 데이터 파괴 변경을 자동 롤백 가능하다고 가정하지 않는다.
4. `infra/aws/cleanup.py`에 이번 manifest/태그 기반 역순 삭제·S3 모든 version 내려받기/무결성 검사·보존 자원 purge·최종 조회를 재사용 가능한 코드로 옮긴다. reset의 기본 완료 기준은 보존 목록 0이다. 실패 후 재실행은 이미 삭제된 대상을 확인하고 남은 단계만 계속한다.
5. `.github/workflows/ikonly-ci.yml`의 deploy/operations job에서 승인된 CI artifact 게시와 update/recover/OIDC 검사를 연결했다. initial/reset은 로컬 `operations.py`를 사용한다. ikonly push에서는 배포된 환경이 있을 때만 변경 서비스 update를 수행하고, reset 후 문서/코드 push가 비용 자원을 자동 재생성하지 않게 한다. initial/reset은 명시적 수동 명령, update는 활성 환경 대상으로 한다. 동일 환경의 update/reset을 같은 lock으로 직렬화한다.
6. 코드가 연결된 뒤 요청된 범위의 로컬 검사와 실제 AWS 첫 배포→부분 업데이트→전체 업데이트→초기화→재배포 수용을 기록한다. 최초 기록 시 자원을 다시 만드는 실행은 하지 않았다. 새 경로 시험 진행 상태는 [구현 검증 기록](../검증산출물/2026-10-12_AWS_배포수명주기_구현검증.md)을 따른다.

## 5. 최초 기록 당시 확인 범위

이번 변경은 선택 업데이트 계획 모듈 및 수명주기 기준을 구체화했다. 모듈 Ruff lint/format 확인과 실제 AWS 자원 삭제/잔존 조회를 수행했다. 업데이트 시나리오의 신규 자동 테스트와 재배포/실제 업데이트는 수행하지 않았으며 성공으로 기록하지 않는다.

## 2026-10-12 구현 연결

사용자는 배포 인증 연결 유지 및 첫 배포 로컬 키 공급을 확정했다. 제어 IAM 역할 두 개와 공유 OIDC 연결은 초기화 대상에서 제외한다. 앱 키/DB/이미지/로그/증거는 초기화 대상이다. 첫 배포와 초기화는 로컬에서 실행하고 GitHub는 ACTIVE 환경 업데이트 및 복구를 담당한다. 정확한 명령·실패 의미는 [운영 가이드 26](26_AWS_배포운영_실행가이드.md)를 따른다.

## 최종 시험 완료

첫 배포(중단 전 인프라 기록 검증 이어받기 포함), 실제 이미지 실패 rollback, 게임 웹 1개 선택 업데이트, GitHub OIDC full 업데이트, full reset/empty reset 재실행을 확인했다. 앱 잔존 18개 영역 0, 제어 역할 2개 유지. 정책 및 image/DB 실패 범위와 증거는 [최종 검증 기록](../검증산출물/2026-10-12_AWS_배포수명주기_구현검증.md)을 따른다.
