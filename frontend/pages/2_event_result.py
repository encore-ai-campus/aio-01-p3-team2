"""Placeholder for the event result page during login-screen development."""

import streamlit as st

if not st.session_state.get("logged_in"):
    st.switch_page("app.py")

st.title("진행 상태 / 결과")
st.info("이벤트 결과 화면은 별도 작업으로 연결됩니다.")
