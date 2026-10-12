# Actions 수명주기 전환과 AWS 초기화

기준: 2026-10-13, ikonly. 사용자는 첫 배포/삭제를 GitHub Actions로 옮기고 현재 AWS 환경을 정리하도록 요청했다.

## 변경

- `.github/workflows/ikonly-lifecycle.yml`: initial / prepare-reset / reset 수동 실행.
- 기존 CI workflow: update에서 auto 또는 특정 서버 선택; 공통 계약 의존성 보호 유지.
- `lifecycle_dispatch`: 로컬 SSM 키 공급, 공개키 등록, encrypted artifact 복호화/보존 확인, OIDC 호출 gate.
- `operations`/`cleanup`: 삭제 없는 정리 준비, 현재 release의 증거 확인 후 전체 초기화, lifecycle Parameter까지 삭제/잔존 검사.
- `bootstrap_oidc`: 앱/지정 lifecycle Parameter 접근 및 SSM 경유 복호화, 소유 security group의 임시 검사 규칙 접근.
- `initial_deployment`: hosted runner의 임시 HTTPS /32 수용 검사와 원래 PC 접근 범위 보존.

원시 로그·시험 저널·로그인 비밀번호·키를 GitHub에 평문 업로드하지 않는다. 암호화 artifact는 7일 보존이며 사용자 PC의 개인키로만 복호화한다. 로컬 보존 확인을 준비한 현재 release/hash와 연결한다. 자세한 실행 순서는 [운영 27](../기획안설계/27_Actions_첫배포_초기화_운영.md)을 따른다.

## 실제 AWS 정리 진행

기존 수용된 release `9ea865291a2d51a184d67ca260aa268e7347cd97`의 일반/시험 환경을 로컬 `operations reset`으로 정리했다. 새 workflow를 배포하기 전 시작한 실행이며 새 hosted reset 성공으로 기록하지 않는다.

로컬 원본은 Git 제외 `test-results/aws-reset-2026-10-13/`에 보존한다. 실제 시험 저널 SHA/byte 검사, S3 object version의 재다운로드 hash 검사, 일반/시험 서비스 drain, 10개 CloudWatch/RDS log group 보관을 완료했다. 5개 stack과 보존 자원 삭제 후 **RESET_COMPLETE / ABSENT / 잔존 17개 항목 0**을 확인했다. 컨트롤러·CloudFormation IAM 역할 및 공유 OIDC는 유지한다. 다른 환경 자원을 계정 전체 초기화 대상으로 다루지 않는다.

## 확인 결과

- 변경 영향 AWS 단위 검사: 40 PASS (암호화/잘못된 키·변조 거부, 잘못된 release/hash의 삭제 거부, prepare 단계의 무삭제, 평문 artifact 금지, 기존 IaC/초기화 재시도 등).
- 해당 코드 Ruff check/format 검사 통과.
- 실제 잔존 조회는 17개 분류이며 모두 빈 배열이다. [잔존 검사 JSON](2026-10-13_AWS_초기화_잔존검사.json)을 기준으로 하며 진행 안내의 18개 표기는 분류 수를 잘못 센 것이다.
- OIDC 컨트롤러 권한 구성 적용 완료. 실제 [OIDC 연결 실행 38323471231](https://github.com/ik-e/multi-agent-autohealing/actions/runs/38323471231)은 SUCCESS / OIDC_CONNECTED / ABSENT / 잔존 0이다.
- 새 lifecycle workflow는 ikonly push로 등록하되 push에서는 실행하지 않는다. 실제 [삭제 확인 guard 실행 38323631335](https://github.com/ik-e/multi-agent-autohealing/actions/runs/38323631335)은 `guard-test` 환경명을 입력해 `Exact environment confirmation is required`로 삭제 전에 거부됐다. 이 실행의 GitHub conclusion=failure는 의도한 음성 검사이며 실제 reset 성공을 뜻하지 않는다.
- [인프라 Linux 검사 38323461065](https://github.com/ik-e/multi-agent-autohealing/actions/runs/38323461065)는 SUCCESS다. [전체 CI 38323461063](https://github.com/ik-e/multi-agent-autohealing/actions/runs/38323461063)는 SUCCESS다. core 617 / PostgreSQL 56 / UI 36 / 운영 이미지 6, 합계 715 PASS이며 자동 업데이트는 INACTIVE / created_resources=false였다.
- 제품 코드/UI/DB 스키마는 바꾸지 않았다. 다른 작업의 `현재_구현상태.md` 및 라우터 변경은 이 커밋에 포함하지 않는다.
- 새 hosted 유료 초기 배포/삭제는 아직 실행하지 않았으며, 다음 첫 배포 때 키 공급→initial→prepare→보존확인→reset 전체를 확인해야 한다.

정리 원본 `result.json`은 RESET_COMPLETE이며 lock 해제와 새 코드의 앱/lifecycle Parameter 잔존 검사까지 다시 확인했다. [직전 첫 배포](2026-10-13_AWS_현행코드_첫배포_검증.md)의 ACTIVE는 당시 기록이며 이번 정리 후 현재 상태로 해석하지 않는다.

후속 사용자 요청으로 임시 SSM 공급 세대/실패 정리를 보완했다. 위 CI source `5ec94ef` 이후 변경의 검증은 [SSM 보완 검증](2026-10-13_SSM_임시공급_세대관리_보완.md)을 별도로 따른다.
