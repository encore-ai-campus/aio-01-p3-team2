# Actions 전체 경로 AWS 검증

상태: 진행 중 · 2026-10-13 · ikonly

## 목표와 사전 기준

GitHub Actions 첫 배포 → 시스템별 업데이트 → 증거 로컬 보존 → 전체 초기화를 실제 AWS에서 검증한다. 배포 중에는 보존 가이드의 정상 요청·에이전트 실패·API/MCP 타임아웃·DB 연결 오류를 기존 로컬 시험 계약으로 시험한다. 실제 OpenAI 호출과 결정적 응답 시험을 구분하고 자동 복구율·복구 기대 사례 성공률·안전 중단을 분리한다. 정상 정책·게임 상태와 시험 DB 격리, HTTPS·RDS TLS, 멱등성·저장 및 서버 수용을 확인한다. 마지막에는 앱/저장/운영 자원 17개 분류와 임시 시크릿 잔존 0, 배포 IAM/OIDC 보존을 확인한다.

## 준비와 발견

- 기존 AWS 인증 유효, 로컬 사용자 서버 유지. 승인 정책 원본 및 CI 38324628321을 선택했다.
- 첫 시도 run 38326013543은 CI와 HEAD 차이가 한글 이름 문서뿐임에도 Git 기본 경로 인용 때문에 소스 불일치로 잘못 거절됐다. 인프라 생성 단계는 실행되지 않았다.
- Git diff를 NUL 구분 UTF-8 경로로 읽고 마지막 빈 항목을 제외하도록 수정했다. 코드 변경 거절 계약은 유지한다. 기존 workflow 회귀 검사에 경로 옵션 검사를 추가했고 해당 단위 시험 20 PASS, Ruff 통과.
- 실패 실행의 임시 공급 세대 회수를 완료했다. 원본은 Git 제외 test-results/aws-actions-full-2026-10-13/에 보존한다.

전체 실제 실행 결과는 완료 후 추가한다. 준비·단위 시험을 유료 전체 경로 수용으로 표현하지 않는다.

- 두 번째 run 38326676258은 첫 경로 수정의 heredoc 들여쓰기 오류로 claim 전 중단됐다. 이 수정 오류를 보존하고, workflow Python 본문 컴파일 검사를 기존 회귀 항목에 추가했다. 임시 공급은 아직 claim되지 않았으므로 다음 실행에서 사용한다. 유료 인프라는 생성되지 않았다.

## 실제 첫 배포 진행

CI 38326771862(source e476376)는 전체 승인 성공. Actions initial 38327297896이 실제 OIDC로 실행되어 network/access/data/application 스택 생성 완료, RDS available을 확인했다. 아직 서비스 최종 수용 전이므로 완료로 판정하지 않는다. 다음 시스템별 배포 시험은 기능 코드 변경 없이 이 진행 문서를 포함하는 새 Git revision의 검증 이미지를 사용한다. 이미지 revision/digest 및 선택 역할 변경, 나머지 역할 보존을 실제 대조한다. 시간은 Actions/job/step과 CloudFormation 이벤트에서 기록한다.

## 첫 배포와 첫 선택 업데이트 수용

- Actions initial 38327297896: INITIAL_ACCEPTED, release 3f4f2719ff81d09d02a5d180751c28691e38a8df, 5 stack/6 host 및 HTTPS 수용. 실행 27분 31초, 대기 4초. 암호화 artifact를 로컬 복호화하고 SHA256 ff7c92f738478d8dedfa90b28fea6687d156d08df2946190ca0656bfffe78db1을 확인했다.
- Actions update 38328823504: game-web만 ACCEPTED. API/MCP/기획 웹의 service manifest 5개가 이전과 정확히 같고 6개 instance ID도 유지됐다. 기능 코드가 같은 문서 revision 4194f66 이미지로 선택 배포 경로를 검증했다.
- 실제 RDS DB: eventquest_demo / eventquest_fault_test, SSL=true 각각 확인. fault API 원래 서버 환경에는 OpenAI 키가 없다. 추가 메타데이터 도구의 날짜 JSON 변환 실패는 도구에 default=str을 추가해 재확인했고 앱 코드는 변경하지 않았다.
- 다음 기획 웹 선택 배포도 새 기록 revision의 승인 이미지를 사용한다. SHA별 immutable ECR tag 계약 때문에 같은 SHA를 새로 빌드한 다른 archive로 덮어쓰지 않는다.
