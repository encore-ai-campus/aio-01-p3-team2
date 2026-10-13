# Actions 첫 배포·초기화 운영

기준: 2026-10-13, `ikonly`. [운영 가이드 26](26_AWS_배포운영_실행가이드.md)의 첫 배포·초기화 전체를 로컬에서 수행하는 기본 경로를 이 문서로 변경한다. 기존 로컬 명령은 비상 운영 경로로 유지한다. 상위 수명주기·역할별 업데이트 계약은 [25](25_AWS_배포수명주기_정리.md)를 따른다.

## 실행 책임

| 작업 | 실행 위치 | 조건 |
|---|---|---|
| 첫 배포 키 공급 | 사용자 PC → SSM SecureString | AWS 로그인, backend/.env, 승인 정책 원본 SHA256 |
| 첫 배포 | Actions `Ikonly first deployment and reset` → initial | ikonly, 정확한 환경명, 성공한 현행 CI run ID |
| 자동 변경 배포 | 기존 CI workflow → update | ikonly push, ACTIVE, 영향 있는 역할만 |
| 서버 선택 배포 | 기존 CI workflow → update + target_service | auto 또는 6개 역할 중 선택; 필요한 의존 서버를 빠뜨리면 거부 |
| 전체 서버 업데이트·실패 복구 | 기존 CI workflow → full / recover | 기존 수용·복구 계약 유지 |
| 정리 준비 | 새 lifecycle workflow → prepare-reset | 실제 수용된 환경, 정확한 환경명 |
| 증거 보존 확인 | 사용자 PC | 암호화 artifact 다운로드·복호화·무결성 확인 |
| 전체 초기화 | 새 lifecycle workflow → reset | 현재 동결 release의 로컬 보존 확인과 SHA256 |

GitHub Actions가 인프라 생성·서비스 배포·AWS 삭제를 실행한다. 로컬에서는 키 공급과 증거 보존/확인만 한다. 배포 인증용 IAM 두 역할·공유 OIDC는 전체 초기화 후 유지한다.

## 첫 배포

1. `IKONLY_CONFIG`를 현재 PC 공인 IP와 맞춘다. 도메인 없는 HTTPS IP 접속을 유지한다.
2. 사용자 PC에서 아래 명령으로 키와 승인 정책을 공급한다. 입력 파일은 Git 제외 경로이며 값은 명령행에 넣지 않는다.

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.lifecycle_dispatch stage --config test-results/aws-first-current-2026-10-13/operation-config.json --env-file backend/.env --policy-file test-results/aws-a6/approved-policy.json --approved-policy-sha256 b175379609f2f9615b5b1f49c9140a66c945dd0c11f00e57b927bf708b9fa61b --private-key test-results/aws-actions/evidence-private.pem --output test-results/aws-actions/stage
```

환경의 키·모델·actor·토큰/비용 설정과 승인 정책 원본을 세대별 Standard SecureString으로 나누어 보관한다. 모든 조각을 읽어 payload hash를 확인한 뒤 활성 세대 포인터를 갱신한다. 새 등록이 실패하면 이전 활성 세대를 보존하고 실패한 세대만 제거한다. 공급 계약은 24시간 후 사용을 거부하며, 성공/배포 실행 실패 시 실행이 소유한 세대와 포인터를 삭제한다. 만료는 자동 삭제를 뜻하지 않는다. 앱이 이미 수용된 상태에서는 재공급하지 않는다.

첫 배포 workflow는 AWS 인증 후 세대를 claim하고 CI artifact 검사/다운로드 실패 시에도 always 단계에서 해당 run의 세대만 정리한다. workflow 자체가 실행되지 않았거나 OIDC 인증에 실패하면 AWS에 접근할 수 없으므로 로컬에서 `lifecycle_dispatch clear-supply --config <config.json> --confirm-environment ikonly-demo --output <local-folder>`로 공급을 삭제한다. 다음 stage는 작업 lock 아래에서 만료·미완성·미활성 잔여 세대와 구 프로토콜 조각을 회수한다. claim한 workflow가 실행 중이면 재공급·sweep·키 교체를 거부한다. 삭제 실패는 generation과 잔존 count만 기록하고 실패로 반환한다. [SSM 보완 검증](../검증산출물/2026-10-13_SSM_임시공급_세대관리_보완.md)을 따른다.

3. Actions에서 `Ikonly first deployment and reset`을 선택하고 branch `ikonly`, operation `initial`, confirm_environment `ikonly-demo`, ci_run_id에 성공 CI 번호를 입력한다. CI source와 현재 실행 코드의 차이가 있으면 거부한다. 문서만의 차이는 허용한다.
4. 기존 CI의 images/verified artifact를 그대로 내려받아 원래 검증·인프라 Add-only 검토·역할별 수용을 수행한다. runner는 HTTPS 수용 검사 중에만 자신의 공인 IP /32를 두 웹의 443 규칙에 추가하고 finally에서 제거한다. 사용자 PC 허용 범위를 바꾸지 않는다.
5. `lifecycle-encrypted-<run>-<attempt>` artifact를 로컬에 다운로드한다. Actions에는 로그인 정보·원시 로그를 RSA-OAEP(SHA256)+AES-256-GCM으로 암호화한 파일만 업로드한다. 원문 및 복호화 개인키는 업로드하지 않는다. artifact 보존 기간은 7일이므로 성공 직후 다운로드한다.

```powershell
gh run download <run-id> --repo ik-e/multi-agent-autohealing --name lifecycle-encrypted-<run-id>-1 --dir test-results/aws-actions/initial-download
backend/.venv/Scripts/python.exe -m infra.aws.lifecycle_dispatch unpack-evidence --config test-results/aws-first-current-2026-10-13/operation-config.json --private-key test-results/aws-actions/evidence-private.pem --evidence-file test-results/aws-actions/initial-download/evidence.bin --output test-results/aws-actions/initial-evidence
```

복호화한 폴더에서 web-login.txt, 두 공개 CA, result.json을 확인한다. 새 CA를 Windows에 설치하는 것은 사용자 로컬 절차다. 개인키를 잃으면 artifact를 복호화할 수 없으므로 PC에 안전하게 보존한다. runner 디스크의 원문 작업 파일은 artifact로 올리지 않는다.

## 정리: 보존 확인 후 삭제

1. 이전 배포가 로컬 first deployment여서 증거 공개키가 없으면 먼저 `register-evidence-key`로 로컬 개인키를 만들고 공개키만 등록한다.

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.lifecycle_dispatch register-evidence-key --config test-results/aws-first-current-2026-10-13/operation-config.json --private-key test-results/aws-actions/evidence-private.pem --output test-results/aws-actions/key
```

2. 새 workflow에서 operation `prepare-reset`, confirm_environment `ikonly-demo`를 실행한다. 작업 유입 차단·drain 후 RESETTING으로 동결하고 저널/S3 객체/CloudWatch/RDS 로그를 보관한다. **이 단계에서는 AWS 자원을 삭제하지 않는다.** 서비스가 정지되므로 사용자 테스트를 이어갈 수 없고 자원 비용은 아직 발생한다.
3. 암호화 artifact를 PC에 내려받고 아래 명령으로 복호화·무결성 확인한다. 현재 동결 release와 같은 archive일 때에만 SSM에 비밀값 없는 로컬 보존 확인을 기록한다.

```powershell
backend/.venv/Scripts/python.exe -m infra.aws.lifecycle_dispatch confirm-evidence --config test-results/aws-first-current-2026-10-13/operation-config.json --private-key test-results/aws-actions/evidence-private.pem --evidence-file test-results/aws-actions/reset-download/evidence.bin --output test-results/aws-actions/reset-evidence
```

4. 새 workflow에서 operation `reset`, confirm_environment `ikonly-demo`, evidence_sha256에 확인 명령이 출력한 값 전체를 입력한다. hash와 release가 일치하지 않으면 삭제하지 않는다. 삭제 순서는 observability → application → data → access → network다. 이미지·스냅샷·로그·S3·앱 Parameter·키 공급/증거 확인용 lifecycle Parameter도 제거한다.
5. 삭제 도중의 추가 종료 로그도 마지막 암호화 artifact로 보존한다. 이를 로컬에 내려받고 `unpack-evidence`로 복호화한다. 초기 증거는 이미 PC에 있으므로 runner 실패가 기존 보존 증거를 지우지 않는다.
6. `RESET_COMPLETE`와 residual-check의 모든 배열이 빈 것인지 확인한다. 자동 업데이트는 ABSENT를 다시 생성하지 않는다.

실패한 prepare/reset은 같은 workflow의 같은 출력 경로로 재시도한다. 상태가 RESETTING인 동안 업데이트하지 않는다. 다른 동결 release의 증거/확인값을 재사용하지 않는다. 초기 배포가 수용되기 전에 실패하면 기존 소유권 marker/token 조건에 맞는 새 환경만 자동 정리하며, 그 실행의 암호화 증거를 내려받는다.

## 권한과 검증 한계

OIDC는 ikonly 저장소 subject만 허용한다. 컨트롤러의 키 접근은 앱/지정 lifecycle Parameter로 제한하고 KMS 복호화는 서울 SSM 경유로 제한한다. HTTPS 검사 규칙 변경은 소유 태그가 일치하는 security group만 허용한다. 런타임 서버의 읽기 권한은 기존 역할별 범위를 유지한다.

최신 [Actions 전체 경로 AWS 검증](../검증산출물/2026-10-13_Actions_전체경로_AWS_검증.md)에서 hosted initial/각 서버 update/prepare-reset/reset의 실제 수용, 배포 환경 장애 시험, 로컬 증거 보존 및 잔존 0을 확인했다. 실행 시간과 취소·재개도 구분해 기록한다. 이전 [전환·초기화 기록](../검증산출물/2026-10-13_Actions_수명주기_전환과_AWS_초기화.md)은 당시 근거로 보존한다.

## 대규모 시험 증거 회수

저널은 원격 호스트에서 한 번 만든 gzip 파일을 독립된 12,000-byte 조각으로 최대 3개 동시 읽는다. 조각당 base64 출력은 16,000문자 이내로 SSM 출력 한도보다 작다. offset 순서로 합치고 각 조각 길이, 전체 길이와 SHA256을 확인한 후에만 archive를 저장한다. archive 생성·상태 변경·자원 삭제는 기존 순서를 유지한다.

2026-10-13 실제 준비에서 1,515,363-byte 저널의 기존 6,000-byte 순차 전송(253회)을 발견해 삭제 전 실행을 취소했다. 종료된 run의 소유권을 확인한 뒤 정확한 lock만 해제하고 RESETTING의 동일 frozen release/output 경로에서 재개한다. 취소는 자동 정상화로 집계하지 않는다. [전체 경로 검증](../검증산출물/2026-10-13_Actions_전체경로_AWS_검증.md)에 정상 실행과 취소·재개 시간을 따로 남긴다.
