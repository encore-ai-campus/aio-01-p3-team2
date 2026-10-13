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

## 배포 환경 기능·정책 시험과 시간 기록

- 기획 웹 선택 업데이트 38329923527: plan-web만 ACCEPTED. 다른 5개 역할의 manifest와 6개 instance ID 유지. 기능 코드가 같은 기록 revision 6481383으로 검증했다.
- 실제 OpenAI 기획 → 승인 → 게임 등록 → 선택한 두 공간 클리어 → 첫 보상 및 중복 방지까지 통과. gpt-4.1-mini Planner/Critic 2회 호출, RDS 저장 원문 암호화·복호화 후 hash 일치 2건, SSL=true를 별도로 확인했다.
- 배포 fault API의 정책 시험 137건: PASS 137, FAIL/INCONCLUSIVE/미실행 0. 기본 28 / 안전 중단 27 / 재시도 허용 46 / 현재 활성 36. 결정적 응답 시험이며 OpenAI 호출은 없다. 실제 Supervisor의 검출·정책·복구를 사용한다. DB 장애는 상황 주입으로 실제 RDS 중단과 구분한다.
- 사전 명세의 오류 모집단 127건 중 자동 정상화 51건(40.1575%), 복구 기대 사례 51/51, 평균 복구 1.0262초(51건, 누락 0). 안전 중단을 자동 정상화에 포함하지 않는다.
- 독립 DB 재조회: 정상 정책·정상 게임 유지, 시험 게임 유지, 시험 정책 version/content 원복, fault 서버 부모 프로세스 OpenAI 키 없음. 원본 final.json의 PENDING 표시는 별도 preservation-after-137.json 증거로 보충한다.
- 실제 LLM 시험 준비 중 원격 실행 문자열과 이미지에 제외된 시험 모듈 문제를 발견했다. 운영 기능 실패로 집계하지 않고 도구 준비 실패로 보존한다. 도구는 배포 source의 Git 원문을 별도 자식 프로세스에 공급한다.

시간은 run/job/step UTC 기록으로 산출하고 전체 워크플로우와 실제 deploy job을 구분한다.

| 작업 | 전체 실행 | deploy job | 비고 |
|---|---:|---:|---|
| 첫 배포 | 27분 31초 | 27분 31초 | CI 사전 승인 별도, 시작 대기 4초 |
| game-web 업데이트 | 11분 16초 | 2분 41초 | CI 및 첫 배포 완료 대기 포함 |
| plan-web 업데이트 | 7분 47초 | 2분 36초 | 전체 CI 포함 |

API/MCP 업데이트, 실제 LLM 복구, 브라우저 시연, 초기화는 아직 완료 판정 전이다.

## 실제 LLM·MCP 복구와 Edge 시연 수용

실제 LLM 시험 13 PASS / FAIL·INCONCLUSIVE 0. 정상 4건(마지막 재가용성 재확인 포함), 오류 9건. 오류 중 자동 정상화 6/9(66.67%), 사전 복구 기대 6/6(100%), 지속 오류 안전 중단 3/3, 평균 복구 5.2883초. 실제 provider 호출 27, 입력 128,756 / 출력 5,356 token, 설정 가격 기준 $0.060072. 실제 청구 금액과 구분한다.

시험 역할·DB는 fault-api / eventquest_fault_test. 배포 부모 API는 deterministic·키 없음 계약을 유지했다. 운영 이미지에서 제외한 4개 시험 모듈은 해당 배포 source e476376의 Git 원문을 메모리에 공급했다. 자식 CLI만 실제 호출을 허용하는 시험 fixture로 환경 판정을 대체하되, 시작 전 실제 CloudDatabase TLS·DB identity·runtime 권한 검증을 하고 연결별 validate_connection은 유지했다. 운영 fault API에 LLM을 켠 시험으로 표현하지 않는다. 운영 main API의 실제 LLM→게임 반영은 앞 절의 별도 정상 시험이다.

응답 변형과 timeout은 시험 상황 주입이며 실제 OpenAI 사업자 장애가 아니다. MCP는 실제 private fault MCP 앞의 소유 loopback bridge/proxy로 연결 끊김·지연·503을 발생시켜 실제 client 검출과 복구를 시험했다. 부모 서버 설정·운영 기능 코드는 변경하지 않았다. 임시 SecureString과 IAM grant 삭제를 독립 조회했고 시험 정책·게임 원복/CLEAN을 확인했다.

배포 이미지 파일 hash를 Windows 작업 파일이 아닌 해당 source Git blob과 비교했다(CRLF 차이 방지). 전부 일치. RDS의 실제 prompt/response 암호문 27건은 복호화 hash 일치, TLS=true를 별도로 확인했다. 준비 도구의 문자열·누락 모듈·변수명·cloud 격리 guard 중단은 본 시험 13건과 별도로 보존했다.

| 사례 | 결과 | 종료 상태 | 실제 LLM 호출 |
|---|---|---|---:|
| normal_single | PASS | WAITING_APPROVAL | 2 |
| normal_multi | PASS | WAITING_APPROVAL | 2 |
| normal_unsupported | PASS | COMPLETED | 2 |
| planner_format_once | PASS | WAITING_APPROVAL | 3 |
| planner_explanation_once | PASS | WAITING_APPROVAL | 4 |
| llm_timeout_once | PASS | WAITING_APPROVAL | 2 |
| planner_format_always | PASS | SAFE_STOP | 4 |
| llm_timeout_always | PASS | SAFE_STOP | 0 |
| mcp_disconnect_once | PASS | WAITING_APPROVAL | 2 |
| mcp_delay_once | PASS | WAITING_APPROVAL | 2 |
| mcp_503_once | PASS | WAITING_APPROVAL | 2 |
| mcp_503_always | PASS | SAFE_STOP | 0 |
| normal_single | PASS | WAITING_APPROVAL | 2 |

Edge 배포 화면 10 PASS(정상 화면 2 + 복구 시연 8). 실제 form 카탈로그, 정책 격리, 게임 Phaser·탭, custom brief, 재시도·기획 수정·MCP 재시도·폴백·지속 오류 안전 중단, 실제 DB 저장 결과, 실행 경로/복구 비교, 데스크톱·390px 모바일, 새로고침 중복 방지를 확인했다. 브라우저는 임시 인증서를 exception으로 열었고, API/Actions 수용은 실제 CA+hostname 인증을 별도로 통과했다. 모바일 큰 그래프는 가로 이동이 필요한 표시이며 상단 LOCAL RUNTIME 배지는 배포 환경 구분을 정확히 나타내지 않는 기존 표현 한계다. 기능 수용과 표현 한계를 구분한다.

정책 시험 137건의 terminal 분포: WAITING_APPROVAL 53 / SAFE_STOP 55 / FAILED 21 / REQUEST_BLOCKED 4 / COMPLETED 4. 정상화 51 외 승인 대기 2는 상태 충돌 시험이므로 자동 복구 분자에 넣지 않는다. 실패·중단도 사전 기대 정책과 일치하면 시험 PASS일 수 있다.
