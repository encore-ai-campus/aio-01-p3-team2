"""Shared dashboard shell for authenticated Streamlit pages."""

from html import escape
from pathlib import Path

import streamlit as st

from services.session import clear_task

CSS_PATH = Path(__file__).resolve().parents[1] / "styles" / "dashboard.css"


def setup_page(title: str, subtitle: str) -> None:
    """Set page metadata, require demo login, and draw shared navigation."""
    st.set_page_config(
        page_title=f"{title} | OPONG HEROES",
        page_icon="👑",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not st.session_state.get("logged_in"):
        st.switch_page("app.py")
    st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand"><span class="brand-crown">♛</span>'
            "<div><strong>OPONG HEROES</strong><small>LIVE OPS</small></div></div>",
            unsafe_allow_html=True,
        )
        st.page_link("pages/1_event_create.py", label="이벤트 생성", icon="⭐")
        st.page_link("pages/2_event_result.py", label="진행 상태 / 결과", icon="📋")
        st.page_link("pages/3_ops_debug.py", label="운영 · 디버그", icon="🛠️")
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        st.caption(f"데모 운영자 · {st.session_state.get('actor_id', '')}")
        if st.button("로그아웃", key="sidebar-logout", width="stretch"):
            clear_task()
            st.session_state["logged_in"] = False
            st.session_state.pop("actor_id", None)
            st.session_state.pop("login-id", None)
            st.switch_page("app.py")

    heading, account = st.columns([4, 1])
    with heading:
        st.markdown(
            f'<div class="page-heading"><h1>{escape(title)}</h1>'
            f"<span>{escape(subtitle)}</span></div>",
            unsafe_allow_html=True,
        )
    with account:
        st.markdown(
            f'<div class="account-chip"><span class="account-avatar">●</span>'
            f"{escape(str(st.session_state.get('actor_id', '')))} | 운영팀</div>",
            unsafe_allow_html=True,
        )


def section_heading(number: int, title: str, description: str = "") -> None:
    detail = f"<p>{escape(description)}</p>" if description else ""
    st.markdown(
        f'<div class="section-heading"><span>{number}</span><div><h2>{escape(title)}</h2>{detail}</div></div>',
        unsafe_allow_html=True,
    )


def card_title(icon: str, title: str) -> None:
    st.markdown(
        f'<h3 class="card-title"><span>{escape(icon)}</span>{escape(title)}</h3>',
        unsafe_allow_html=True,
    )
