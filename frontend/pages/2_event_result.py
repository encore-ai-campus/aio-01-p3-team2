"""Current event plan, validation, AutoHealing, and human approval."""

from html import escape
from typing import Any

import streamlit as st

from components.autohealing_view import render_autohealing
from components.event_result import render_event_result, render_rule_validation
from components.layout import card_title, setup_page
from components.workflow_status import render_workflow_status
from services.backend_client import ApiError, get_task, get_trace, submit_approval
from services.session import remember_task


def refresh_task(task_id: str, *, show_error: bool = True) -> None:
    if task_id.startswith("DEMO-"):
        st.info("데모 결과는 Backend에서 다시 조회하지 않습니다.")
        return
    try:
        remember_task(get_task(task_id))
    except ApiError as exc:
        if show_error:
            st.error(f"Task를 다시 읽지 못했습니다: {exc} ({exc.code})")
    try:
        st.session_state["trace_response"] = get_trace(task_id)
    except ApiError as exc:
        if show_error:
            st.warning(f"Trace를 읽지 못했습니다: {exc} ({exc.code})")


def perform_approval(task: dict[str, Any], action: str, comment: str | None = None) -> None:
    if task.get("_demo_preview") is True or str(task.get("task_id", "")).startswith("DEMO-"):
        st.error("데모 결과는 승인 API로 전송할 수 없습니다.")
        return
    with st.spinner("운영자 결정을 처리하고 있습니다."):
        try:
            response = submit_approval(
                str(task["task_id"]),
                actor_id=str(st.session_state["actor_id"]),
                result_version=int(task["result_version"]),
                action=action,
                comment=comment,
            )
        except ApiError as exc:
            if exc.status == 409 and exc.code == "STALE_RESULT_VERSION":
                refresh_task(str(task["task_id"]), show_error=False)
                st.warning("결과 버전이 변경됐습니다. 최신 기획안을 확인한 후 다시 결정해주세요.")
            else:
                st.error(f"승인 요청을 처리하지 못했습니다: {exc} ({exc.code})")
                if exc.may_have_completed:
                    st.info("요청이 처리되었을 수 있습니다. 새로고침으로 최신 Task를 확인해주세요.")
            return
    remember_task(response)
    refresh_task(str(task["task_id"]), show_error=False)
    st.session_state["revision_open"] = False
    st.rerun()


def render_approval(task: dict[str, Any], *, demo_preview: bool = False) -> None:
    with st.container(key="panel-approval", border=True):
        card_title("●", "운영 담당자 최종 승인")
        if demo_preview:
            st.info("데모 결과에서는 승인·수정 요청·거절을 전송하지 않습니다.")
            return
        allowed = (
            task.get("workflow_status") == "WAITING_APPROVAL"
            and task.get("approval_status") == "PENDING"
            and task.get("critic_status") == "PASS"
            and isinstance(task.get("result_version"), int)
        )
        if not allowed:
            st.info("현재 상태에서는 승인 요청을 보낼 수 없습니다.")
            return
        st.caption(f"현재 결과 버전 v{task['result_version']}을 확인한 뒤 결정해주세요.")
        approve_col, revision_col, reject_col = st.columns(3)
        with approve_col:
            if st.button("✓  승인", key="approve", type="primary", width="stretch"):
                perform_approval(task, "APPROVE")
        with revision_col:
            if st.button("✎  수정 요청", key="revision-toggle", width="stretch"):
                st.session_state["revision_open"] = not st.session_state.get("revision_open", False)
        with reject_col:
            if st.button("×  거절", key="reject", width="stretch"):
                perform_approval(task, "REJECT")
        if st.session_state.get("revision_open"):
            with st.form("revision-comment-form"):
                comment = st.text_area(
                    "수정 요청 내용 *",
                    max_chars=500,
                    placeholder="수정할 날짜와 내용을 구체적으로 적어주세요.",
                )
                if st.form_submit_button("수정 요청 제출", type="primary"):
                    if not comment.strip():
                        st.error("수정 요청 내용을 입력해주세요.")
                    else:
                        perform_approval(task, "REQUEST_REVISION", comment.strip())


setup_page("진행 상태 / 결과", "AI 기획안과 검증·복구 결과를 확인하고 최종 결정합니다.")

task_id = st.session_state.get("task_id")
task: dict[str, Any] | None = st.session_state.get("task_response")
demo_preview = (isinstance(task, dict) and task.get("_demo_preview") is True) or str(
    task_id or ""
).startswith("DEMO-")
if not task_id:
    st.info("진행 중인 Task가 없습니다. 이벤트를 생성하면 결과가 여기에 표시됩니다.")
    st.page_link("pages/1_event_create.py", label="이벤트 생성으로 이동", icon="⭐")
    st.stop()

if not isinstance(task, dict):
    if demo_preview:
        st.warning("데모 결과가 현재 세션에 없습니다. 이벤트 생성 화면에서 다시 실행해주세요.")
        st.stop()
    refresh_task(str(task_id))
    task = st.session_state.get("task_response")
if not isinstance(task, dict):
    st.stop()

if demo_preview:
    st.warning(
        "데모 데이터입니다. AI·게임 데이터·Backend를 실행하지 않은 예시 기획안과 Trace입니다."
    )

header_col, refresh_col = st.columns([5, 1])
with header_col:
    result = task.get("final_result") or task.get("current_result") or {}
    title = (
        result.get("event_name")
        or st.session_state.get("last_brief", {}).get("event_name")
        or "이벤트 결과"
    )
    status = task.get("workflow_status") or "상태 없음"
    status_class = (
        "success" if status == "COMPLETED" else "error" if status == "FAILED" else "waiting"
    )
    demo_badge = '<span class="status-pill waiting">데모 데이터</span>' if demo_preview else ""
    st.markdown(
        '<div class="task-head">'
        f"<h2>{escape(str(title))}</h2>"
        f"{demo_badge}"
        f'<span class="status-pill {status_class}">{escape(str(status))}</span>'
        f'<span class="status-pill">Result v{escape(str(task.get("result_version", 0)))}</span>'
        f'<span class="status-pill">Task ID · {escape(str(task_id))}</span>'
        "</div>",
        unsafe_allow_html=True,
    )
with refresh_col:
    if st.button("최신 상태 조회", key="refresh-task", width="stretch", disabled=demo_preview):
        refresh_task(str(task_id))
        st.rerun()

if task.get("workflow_status") == "FAILED":
    error = task.get("error") or {}
    st.error(f"Workflow 안전 종료: {error.get('message') or '오류 원인을 Trace에서 확인해주세요.'}")
    st.page_link("pages/3_ops_debug.py", label="상세 Trace 보기", icon="🛠️")
elif task.get("workflow_status") == "CANCELLED":
    st.warning("운영자가 이 이벤트를 거절했습니다.")
elif task.get("workflow_status") == "COMPLETED":
    st.success("운영자 승인이 완료됐습니다. 최종 기획안이 확정됐습니다.")

if not demo_preview and st.session_state.get("trace_response") is None:
    try:
        st.session_state["trace_response"] = get_trace(str(task_id))
    except ApiError as exc:
        st.caption(f"Trace를 아직 불러오지 못했습니다: {exc}")

with st.container(key="panel-summary", border=True):
    card_title("◎", "Workflow 진행 기록")
    render_workflow_status(task, st.session_state.get("trace_response"))
    st.caption(
        f"Critic: {task.get('critic_status') or '미확정'} · "
        f"AutoHealing: {len(task.get('autohealing_events') or [])}회 · "
        f"기술 Retry: {task.get('retry_count', 0)}회 · "
        f"자동 수정: {task.get('revision_count', 0)}회"
    )

render_event_result(task)
left_col, right_col = st.columns(2, gap="medium")
with left_col:
    render_rule_validation(task.get("rule_validation"))
    with st.container(key="panel-critic", border=True):
        card_title("✦", "AI 최종 검수 (Critic)")
        critic = task.get("critic_status")
        if critic == "PASS":
            st.success("Critic PASS")
        elif critic:
            st.warning(f"Critic 상태: {critic}")
        else:
            st.info("최종 검수 결과가 없습니다.")
with right_col:
    render_autohealing(task.get("autohealing_events"))
    render_approval(task, demo_preview=demo_preview)

st.page_link("pages/3_ops_debug.py", label="전체 Trace와 Backend 상태 보기", icon="🛠️")
