"""Event brief form that only submits fields in the MVP API contract."""

import os
from typing import Any

import streamlit as st

from components.layout import section_heading

TARGET_LABELS = {
    "new_user": "신규 유저",
    "returning_user": "복귀 유저",
    "all_user": "전체 유저",
}
SOURCE_LABELS = {
    "clash_of_clans": "Clash of Clans 공개 데이터",
    "opong": "오퐁 히어로즈 기존 데이터",
}

FAULT_LABELS = {
    "mission_unreachable_once": "달성 불가능 미션 1회",
    "agent_failure_once": "Agent 실행 오류 1회",
    "primary_tool_error_once": "Primary Tool 오류 1회",
    "mcp_timeout_once": "MCP Timeout 1회",
    "db_connection_error_once": "DB 연결 오류 1회",
    "validation_error_once": "Output Validation 오류 1회",
    "retry_limit_exceeded": "재시도 한도 초과",
}


def show_test_mode() -> bool:
    return os.getenv("SHOW_TEST_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def render_event_form() -> dict[str, Any] | None:
    """Render the brief and return a validated request body after submission."""
    st.session_state.setdefault("brief-name", "")
    st.session_state.setdefault("brief-target", "new_user")
    st.session_state.setdefault("brief-duration", 7)
    st.session_state.setdefault("brief-objective", "")
    st.session_state.setdefault("brief-source", "clash_of_clans")

    with st.form("event-brief-form"):
        with st.container(key="panel-basic", border=True):
            section_heading(1, "이벤트 기본 정보", "어떤 이벤트를 만들지 기본 정보를 입력해주세요.")
            game_source = st.selectbox(
                "게임 데이터 원천 *",
                list(SOURCE_LABELS),
                key="brief-source",
                format_func=SOURCE_LABELS.get,
            )
            if game_source == "clash_of_clans":
                st.caption(
                    "글로벌 공개 상위 순위 플레이어 최대 20명과 클랜 최대 3개의 표본을 사용합니다. "
                    "전체 이용자를 대표하는 통계가 아니며 실제 Clash 계정에 이벤트를 지급하지 않습니다."
                )
            name_col, target_col = st.columns(2)
            with name_col:
                event_name = st.text_input(
                    "이벤트 이름 *",
                    key="brief-name",
                    max_chars=100,
                    placeholder="예: 신규 유저 7일 성장 이벤트",
                )
            with target_col:
                if game_source == "clash_of_clans":
                    target_user = "all_user"
                    st.text_input("이벤트 대상", value="데모 전체 유저", disabled=True)
                else:
                    target_user = st.selectbox(
                        "이벤트 대상 *",
                        list(TARGET_LABELS),
                        key="brief-target",
                        format_func=TARGET_LABELS.get,
                    )
            period_col, objective_col = st.columns(2)
            with period_col:
                duration = st.selectbox(
                    "이벤트 기간 *",
                    list(range(1, 8)),
                    key="brief-duration",
                    format_func=lambda day: f"{day}일",
                )
                st.caption("현재 계약에서 1~7일을 지원합니다.")
            with objective_col:
                objective = st.text_area(
                    "이벤트 목적 *",
                    key="brief-objective",
                    max_chars=500,
                    height=112,
                    placeholder="이 이벤트로 얻고 싶은 운영 목표를 적어주세요.",
                )

        if show_test_mode():
            with st.container(key="panel-options", border=True):
                section_heading(
                    2, "실행 모드", "장애 주입은 Backend 설정에서도 허용되어야 실행됩니다."
                )
                mode = st.radio(
                    "모드",
                    ["normal", "test"],
                    key="selected_mode",
                    format_func=lambda value: "Normal Mode" if value == "normal" else "Test Mode",
                    horizontal=True,
                )
                fault_type = None
                if mode == "test":
                    fault_type = st.selectbox(
                        "장애 주입 유형",
                        list(FAULT_LABELS),
                        key="fault_type",
                        format_func=FAULT_LABELS.get,
                    )
        else:
            mode, fault_type = "normal", None

        submitted = st.form_submit_button(
            "✦  AI로 이벤트 생성하기", type="primary", width="stretch"
        )

    if not submitted:
        return None
    if not event_name.strip() or not objective.strip():
        st.error("이벤트 이름과 목적을 모두 입력해주세요.")
        return None

    result: dict[str, Any] = {
        "event_name": event_name.strip(),
        "target_user": target_user,
        "duration": duration,
        "objective": objective.strip(),
        "game_source": game_source,
    }
    if mode == "test" and fault_type:
        result["fault_type"] = fault_type
    return result
