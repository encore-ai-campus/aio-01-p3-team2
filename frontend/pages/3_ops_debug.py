"""Inspect the current Task's Trace and Backend health."""

from html import escape
from typing import Any

import streamlit as st

from components.layout import card_title, setup_page
from components.trace_viewer import display_time, render_trace, trace_items
from services.backend_client import ApiError, get_health, get_task, get_trace
from services.session import remember_task


def refresh_execution(task_id: str) -> None:
    if task_id.startswith("DEMO-"):
        st.info("데모 Trace는 Backend에서 다시 조회하지 않습니다.")
        return
    try:
        remember_task(get_task(task_id))
    except ApiError as exc:
        st.error(f"Task 조회 실패: {exc} ({exc.code})")
    try:
        st.session_state["trace_response"] = get_trace(task_id)
    except ApiError as exc:
        st.warning(f"Trace 조회 실패: {exc} ({exc.code})")


def check_health() -> None:
    results = {}
    for kind in ("live", "ready"):
        try:
            results[kind] = get_health(kind).get("status", "응답 확인 필요")
        except ApiError as exc:
            results[kind] = f"오류: {exc.code}"
    st.session_state["health_results"] = results


setup_page("운영 · 디버그", "현재 Task의 실행 기록과 Backend 상태를 확인합니다.")

task_id = st.session_state.get("task_id")
task: dict[str, Any] | None = st.session_state.get("task_response")
demo_preview = (isinstance(task, dict) and task.get("_demo_preview") is True) or str(
    task_id or ""
).startswith("DEMO-")
if task_id and not isinstance(task, dict):
    if demo_preview:
        st.warning("데모 결과가 현재 세션에 없습니다. 이벤트 생성 화면에서 다시 실행해주세요.")
        st.stop()
    refresh_execution(str(task_id))
    task = st.session_state.get("task_response")
if task_id and not demo_preview and st.session_state.get("trace_response") is None:
    try:
        st.session_state["trace_response"] = get_trace(str(task_id))
    except ApiError:
        pass

if demo_preview:
    st.warning("데모 데이터입니다. 이 Trace는 실제 Agent·MCP·Backend 실행 기록이 아닙니다.")

header_col, action_col = st.columns([4, 1])
with header_col:
    st.markdown(
        f'<div class="task-head"><h2>현재 실행 Task</h2><span class="status-pill">'
        f"{escape(str(task_id or '없음'))}</span></div>",
        unsafe_allow_html=True,
    )
with action_col:
    if st.button(
        "Task · Trace 새로고침",
        key="ops-refresh",
        width="stretch",
        disabled=not task_id or demo_preview,
    ):
        refresh_execution(str(task_id))
        st.rerun()

trace = st.session_state.get("trace_response")
items = trace_items(trace)

summary_col, health_col = st.columns([2.1, 1], gap="medium")
with summary_col, st.container(key="panel-summary", border=True):
    card_title("◎", "실행 요약")
    if isinstance(task, dict):
        first_time = display_time(items[0].get("time")) if items else "기록 없음"
        last_time = display_time(items[-1].get("time")) if items else "기록 없음"
        st.markdown(
            '<div class="kv-grid">'
            f'<div class="kv-item"><small>Workflow 상태</small>{escape(str(task.get("workflow_status") or "알 수 없음"))}</div>'
            f'<div class="kv-item"><small>Result Version</small>v{escape(str(task.get("result_version", 0)))}</div>'
            f'<div class="kv-item"><small>AutoHealing</small>{len(task.get("autohealing_events") or [])}회</div>'
            f'<div class="kv-item"><small>Trace 건수</small>{len(items)}건</div>'
            f'<div class="kv-item"><small>첫 Trace 시각</small>{escape(first_time)}</div>'
            f'<div class="kv-item"><small>마지막 Trace 시각</small>{escape(last_time)}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("위 시각은 Trace 기록 범위이며 전체 실행 시간으로 계산하지 않습니다.")
    else:
        st.info("현재 Task가 없습니다. 이벤트 생성 후 실행 이력이 표시됩니다.")

with health_col, st.container(key="panel-health", border=True):
    card_title("⛨", "Backend Health")
    if st.button("Live / Ready 확인", key="ops-health", width="stretch", disabled=demo_preview):
        check_health()
    health = st.session_state.get("health_results", {})
    st.write(f"Live: **{health.get('live', '확인 전')}**")
    st.write(f"Ready: **{health.get('ready', '확인 전')}**")
    if demo_preview:
        st.caption("데모 결과에서는 Backend 상태를 조회하지 않습니다.")
    else:
        st.caption("Ready는 Backend 준비 상태입니다. MCP 개별 연결 상태를 뜻하지 않습니다.")

overview_tab, trace_tab, input_tab, mcp_tab, system_tab = st.tabs(
    ["실행 개요", "상세 Trace", "입력 데이터", "MCP 실행 기록", "시스템 실행 기록"]
)
with overview_tab:
    if isinstance(task, dict):
        st.write(f"**Critic:** {task.get('critic_status') or '미확정'}")
        st.write(f"**승인:** {task.get('approval_status') or '미확정'}")
        st.write(f"**기술 Retry:** {task.get('retry_count', 0)}회")
        st.write(f"**자동 수정:** {task.get('revision_count', 0)}회")
        if task.get("error"):
            st.error(task["error"].get("message") or "Workflow 오류가 기록됐습니다.")
    else:
        st.info("표시할 실행 정보가 없습니다.")
with trace_tab:
    render_trace(trace)
with input_tab:
    brief = st.session_state.get("last_brief")
    if brief:
        st.caption(
            "현재 브라우저 세션에 남아 있는 입력값입니다. Backend에 저장된 원 요청 조회 기능은 아닙니다."
        )
        st.json(brief)
    else:
        st.info("이 세션에 보관된 입력 데이터가 없습니다.")
with mcp_tab:
    render_trace(trace, actors={"mcp"}, title="MCP 관련 Trace", key="panel-trace-mcp")
with system_tab:
    render_trace(
        trace,
        actors={"backend", "database", "supervisor"},
        title="Backend · DB · Supervisor Trace",
        key="panel-trace-system",
    )
    st.caption("이 화면은 구조화된 Trace를 보여줍니다. 원시 서버 로그 API는 현재 계약에 없습니다.")
