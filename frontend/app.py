"""Streamlit entry point for the Opong Heroes Live Ops demo."""

import base64
import os
from functools import lru_cache
from pathlib import Path

import streamlit as st

FRONTEND_DIR = Path(__file__).resolve().parent
ASSETS_DIR = FRONTEND_DIR / "assets"
CSS_PATH = FRONTEND_DIR / "styles" / "login.css"


@lru_cache(maxsize=2)
def image_data_uri(path: Path) -> str:
    """Embed bundled artwork without depending on a separate static server."""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def demo_ids() -> set[str]:
    configured = os.getenv("DEMO_ALLOWED_IDS", "liveops01")
    return {value.strip() for value in configured.split(",") if value.strip()}


def render_login() -> None:
    allowed_ids = demo_ids()
    st.session_state.setdefault("login-id", min(allowed_ids) if allowed_ids else "")
    background = image_data_uri(ASSETS_DIR / "login_background.png")
    logo = image_data_uri(ASSETS_DIR / "opong_heroes_logo.png")
    css = CSS_PATH.read_text(encoding="utf-8").replace("__BACKGROUND_DATA_URI__", background)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

    with st.container(key="login-shell", border=False):
        hero_column, form_column = st.columns([1.45, 1], gap="small")

        with hero_column, st.container(key="hero-panel", border=False):
            st.markdown(
                f"""
                <div class="hero-inner">
                  <img class="hero-logo" src="{logo}" alt="Opong Heroes Live Ops" />
                  <div class="hero-heading">
                    더 즐거운 게임 경험을 만드는<br />
                    <span>AI 라이브 운영 파트너</span>
                  </div>
                  <p class="hero-description">
                    데이터와 AI가 함께하는 스마트한 라이브 운영으로<br />
                    지금보다 더 특별한 게임 경험을 만들어갑니다.
                  </p>
                  <div class="hero-features" aria-label="서비스 기능">
                    <div><span class="feature-icon blue">✦</span><strong>AI 기반<br />이벤트 기획</strong></div>
                    <div><span class="feature-icon green">◆</span><strong>자동 검수 및<br />리스크 감지</strong></div>
                    <div><span class="feature-icon violet">▮</span><strong>AutoHealing<br />안정적인 서비스 운영</strong></div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with form_column, st.container(key="form-panel", border=False):
            st.markdown(
                """
                <div class="form-brand">OPONG HEROES <span>|</span> <em>LIVE OPS</em></div>
                <div class="form-intro">
                  <h1>로그인</h1>
                  <p>OPONG HEROES 운영 시스템에 접속하세요.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            actor_id = st.text_input(
                "아이디",
                key="login-id",
                placeholder="아이디를 입력하세요.",
                autocomplete="username",
            )
            st.text_input(
                "비밀번호 (데모에서 사용하지 않음)",
                key="demo-password",
                type="password",
                placeholder="데모 로그인은 아이디만 확인합니다.",
                disabled=True,
            )
            st.caption(
                f"데모 ID: {', '.join(sorted(allowed_ids))} · 비밀번호 검증은 제공하지 않습니다."
            )

            if st.button("로그인  →", key="login-submit", type="primary", width="stretch"):
                if actor_id.strip() in allowed_ids:
                    st.session_state["logged_in"] = True
                    st.session_state["actor_id"] = actor_id.strip()
                    st.rerun()
                else:
                    st.error("등록된 데모 아이디를 입력해주세요.")

            st.markdown(
                """
                <div class="login-notice">
                  <span class="notice-symbol">ⓘ</span>
                  <div><strong>내부 시연용 데모 로그인</strong><br />
                    <span>아이디만 확인하며 실제 사내 인증 기능은 제공하지 않습니다.</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_signed_in() -> None:
    st.switch_page("pages/1_event_create.py")


def main() -> None:
    st.set_page_config(
        page_title="OPONG HEROES | LIVE OPS",
        page_icon="👑",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.session_state.setdefault("logged_in", False)
    if st.session_state["logged_in"]:
        render_signed_in()
    else:
        render_login()


if __name__ == "__main__":
    main()
