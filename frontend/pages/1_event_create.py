"""Placeholder for the event creation page during login-screen development."""

import streamlit as st

if not st.session_state.get("logged_in"):
    st.switch_page("app.py")

st.title("이벤트 생성")
st.info("이벤트 생성 화면은 별도 작업으로 연결됩니다.")
