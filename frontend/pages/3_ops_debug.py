"""Placeholder for the operations page during login-screen development."""

import streamlit as st

if not st.session_state.get("logged_in"):
    st.switch_page("app.py")

st.title("운영 · 디버그")
st.info("운영·디버그 화면은 별도 작업으로 연결됩니다.")
