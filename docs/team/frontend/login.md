# 데모 로그인 화면

`frontend/app.py`는 Opong Heroes Live Ops의 로그인 화면을 제공합니다. 사용자 제공 로고와 배경 이미지는 `frontend/assets/`에 저장하고, 화면 스타일은 `frontend/styles/login.css`에 둡니다.

## 실행

```powershell
pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```

데모에서 허용할 직원 ID는 `DEMO_ALLOWED_IDS` 환경변수에 쉼표로 구분해 지정합니다. 미설정 시 `liveops01`만 허용합니다. `frontend/.env.example`은 변수 이름과 예시 ID를 보여주며 앱이 `.env` 파일을 자동으로 읽지는 않습니다.

로그인은 `st.session_state`에 `logged_in`, `actor_id`를 기록하는 **시연용 화면 게이트**입니다. 비밀번호 입력란은 시안의 배치를 보여주기 위해 비활성화되어 있으며 비밀번호 인증과 세션 간 로그인 유지는 구현하지 않습니다. 실제 사내 인증이나 Backend 승인 권한 검증을 대신하지 않습니다.

로그인 후에는 이벤트 생성 화면으로 이동합니다. 세 하위 페이지는 직접 URL로 접근하더라도 데모 로그인이 없으면 `app.py`로 돌아옵니다. 로그아웃하면 현재 브라우저 세션의 Task 정보를 지웁니다. 각 화면의 구현과 Backend 연결 범위는 [프론트엔드 구현 현황](implementation.md)에 정리했습니다.
