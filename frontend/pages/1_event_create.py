"""Create a game event from the documented MVP brief."""

import json
from uuid import uuid4

import streamlit as st

from components.event_form import TARGET_LABELS, render_event_form
from components.layout import card_title, setup_page
from services.backend_client import (
    ApiError,
    delete_task,
    generate_event,
    get_health,
    get_task,
    get_trace,
    list_tasks,
)
from services.demo_data import build_demo_result
from services.session import clear_task, remember_task

EXAMPLES = (
    {
        "event_name": "신규 유저 7일 성장 이벤트",
        "target_user": "new_user",
        "duration": 7,
        "objective": "신규 유저가 초반 핵심 콘텐츠를 경험하고 게임에 자연스럽게 적응할 수 있도록 성장 동선을 제공합니다.",
    },
    {
        "event_name": "복귀 유저 5일 적응 이벤트",
        "target_user": "returning_user",
        "duration": 5,
        "objective": "복귀 유저가 핵심 콘텐츠에 다시 적응할 수 있도록 단계별 미션을 제공합니다.",
    },
    {
        "event_name": "주말 3일 참여 이벤트",
        "target_user": "all_user",
        "duration": 3,
        "objective": "모든 유저가 주말 동안 게임의 기본 콘텐츠에 참여할 수 있도록 짧은 미션을 구성합니다.",
    },
)
DEMO_SCENARIOS = ("복구 성공", "정상 실행", "복구 실패·안전 종료")
HISTORY_PAGE_SIZE = 5
HISTORY_STATUS_LABELS = {
    "WAITING_APPROVAL": "승인 대기",
    "COMPLETED": "승인 완료",
    "FAILED": "안전 종료",
    "CANCELLED": "거절",
}


def load_example(example: dict[str, str | int], index: int) -> None:
    """Fill only the four fields actually supported by the generation API."""
    st.session_state["brief-name"] = example["event_name"]
    st.session_state["brief-target"] = example["target_user"]
    st.session_state["brief-duration"] = example["duration"]
    st.session_state["brief-objective"] = example["objective"]
    st.session_state["brief-source"] = "opong"
    st.session_state["selected_example_index"] = index


def run_demo() -> None:
    """Open a labeled sample result without making a Backend request."""
    index = st.session_state.get("selected_example_index")
    if not isinstance(index, int) or not 0 <= index < len(EXAMPLES):
        st.warning("먼저 예시를 선택해주세요.")
        return
    open_demo_example(index)


def open_demo_example(index: int) -> None:
    """Open one local example from either the input guide or history preview."""
    example = dict(EXAMPLES[index])
    task, trace = build_demo_result(example)
    st.session_state.pop("pending_create", None)
    st.session_state.pop("last_error", None)
    st.session_state["last_brief"] = example
    st.session_state["trace_response"] = trace
    remember_task(task)
    st.switch_page("pages/2_event_result.py")


def load_saved_tasks() -> None:
    """Fetch the latest Backend Task summaries without mixing in demo fixtures."""
    try:
        response = list_tasks(limit=HISTORY_PAGE_SIZE)
    except ApiError as exc:
        st.session_state["saved_task_error"] = {
            "message": str(exc),
            "code": exc.code,
            "status": exc.status,
        }
        st.session_state["saved_task_items"] = []
        st.session_state["saved_task_loaded"] = True
        return

    items = [item for item in response["tasks"] if not item["task_id"].startswith("DEMO-")]
    st.session_state["saved_task_items"] = items
    st.session_state["saved_task_error"] = None
    st.session_state["saved_task_loaded"] = True


def open_saved_task(task_id: str) -> None:
    """Reopen a persisted Task with the existing result and Trace screens."""
    with st.spinner("저장된 기획안을 불러오고 있습니다."):
        try:
            task = get_task(task_id)
        except ApiError as exc:
            st.error(f"기획안을 불러오지 못했습니다: {exc} ({exc.code})")
            return
        if task.get("task_id") != task_id:
            st.error("Task 상세 응답의 ID가 선택한 이벤트와 일치하지 않습니다.")
            return
        try:
            trace = get_trace(task_id)
        except ApiError:
            trace = None

    st.session_state.pop("last_brief", None)
    st.session_state["trace_response"] = trace
    remember_task(task)
    st.switch_page("pages/2_event_result.py")


def delete_saved_task(task_id: str) -> None:
    """Remove the selected Backend Task after the operator confirms the choice."""
    with st.spinner("저장된 기획안과 관련 기록을 삭제하고 있습니다."):
        try:
            delete_task(task_id, actor_id=str(st.session_state["actor_id"]))
        except ApiError as exc:
            st.error(f"기획안을 삭제하지 못했습니다: {exc} ({exc.code})")
            return
    if st.session_state.get("task_id") == task_id:
        clear_task()
    st.session_state.pop("pending_delete_task_id", None)
    st.session_state["saved_task_notice"] = "기획안과 관련 기록을 삭제했습니다."
    load_saved_tasks()
    st.rerun()


def create_event(brief: dict[str, str | int]) -> None:
    test_mode = "fault_type" in brief
    signature = json.dumps(
        {"brief": brief, "test_mode": test_mode}, ensure_ascii=False, sort_keys=True
    )
    pending = st.session_state.get("pending_create")
    if pending and pending.get("signature") == signature:
        payload = pending["payload"]
    else:
        payload = {
            "actor_id": st.session_state["actor_id"],
            **brief,
            "idempotency_key": str(uuid4()),
        }
        st.session_state["pending_create"] = {"signature": signature, "payload": payload}

    with st.spinner("이벤트 생성 요청을 처리하고 있습니다. 완료까지 기다려주세요."):
        try:
            task = generate_event(payload, test_mode=test_mode)
        except ApiError as exc:
            st.session_state["last_error"] = exc.code
            st.error(f"{exc} ({exc.code})")
            if exc.may_have_completed:
                st.caption("같은 내용을 다시 제출하면 동일한 idempotency_key로 재시도합니다.")
            else:
                st.session_state.pop("pending_create", None)
            if isinstance(exc.detail, list):
                for item in exc.detail:
                    if isinstance(item, dict):
                        st.caption(f"{item.get('field', '입력값')}: {item.get('reason', '')}")
            return

    if not task.get("task_id"):
        st.error("Backend 응답에 task_id가 없습니다. 응답 계약을 확인해주세요.")
        return
    st.session_state.pop("pending_create", None)
    st.session_state.pop("trace_response", None)
    st.session_state["last_brief"] = brief
    st.session_state.pop("last_error", None)
    st.session_state["saved_task_loaded"] = False
    remember_task(task)
    st.switch_page("pages/2_event_result.py")


setup_page("이벤트 생성", "AI가 게임 데이터 기반으로 이벤트를 자동 기획합니다.")

main_col, side_col = st.columns([2.25, 1], gap="medium")
with main_col:
    brief = render_event_form()
    st.markdown(
        '<p class="muted-note">계획된 흐름: MCP 기획 데이터 조회 → 이벤트 초안 생성 → MCP 검증 데이터 조회 → 규칙 검증 → 필요 시 자동 수정 → 최종 검수. 실제 실행 순서는 결과 Trace에서 확인하세요.</p>',
        unsafe_allow_html=True,
    )
    if brief is not None:
        create_event(brief)

    if not st.session_state.get("saved_task_loaded"):
        load_saved_tasks()
    with st.container(key="panel-saved-tasks", border=True):
        title_col, refresh_col = st.columns([4, 1])
        with title_col:
            card_title("▤", "저장된 이벤트 기획안")
        with refresh_col:
            if st.button("새로고침", key="saved-tasks-refresh", width="stretch"):
                load_saved_tasks()
        st.caption("Backend에 저장된 최근 Task 목록을 표시합니다.")

        notice = st.session_state.pop("saved_task_notice", None)
        if notice:
            st.success(notice)

        error = st.session_state.get("saved_task_error")
        if error:
            if error.get("status") == 404:
                st.info(
                    "Backend에 이벤트 목록 조회 API가 아직 없습니다. 구현 후 저장된 목록이 표시됩니다."
                )
            else:
                st.warning(
                    f"저장된 이벤트를 불러오지 못했습니다: {error['message']} ({error['code']})"
                )
        else:
            items = st.session_state.get("saved_task_items") or []
            for item in items:
                task_id = item["task_id"]
                name = item.get("event_name") or "이름 없는 이벤트"
                status = str(item.get("workflow_status") or "상태 미기록")
                created_at = str(item.get("created_at") or "")
                summary = " · ".join(
                    part
                    for part in (
                        HISTORY_STATUS_LABELS.get(status, status),
                        created_at.replace("T", " ")[:16] if created_at else "생성일 미기록",
                    )
                )
                with st.container(border=True):
                    info_col, action_col, delete_col = st.columns(
                        [6, 1.4, 0.9], vertical_alignment="center"
                    )
                    with info_col:
                        st.write(f"{name} · {summary}")
                    with action_col:
                        label = "기록 보기" if status == "FAILED" else "기획안 보기"
                        if st.button(label, key=f"saved-task-{task_id}", width="stretch"):
                            open_saved_task(task_id)
                    with delete_col:
                        if st.button(
                            "삭제",
                            key=f"delete-task-{task_id}",
                            width="stretch",
                            disabled=status in {"RUNNING", "RETRYING"},
                        ):
                            st.session_state["pending_delete_task_id"] = task_id
                    if st.session_state.get("pending_delete_task_id") == task_id:
                        st.warning(f"'{name}' 기획안과 Trace·Tool 기록을 영구 삭제합니다.")
                        confirm_col, cancel_col = st.columns([1, 1])
                        with confirm_col:
                            if st.button("영구 삭제 확인", key=f"confirm-delete-{task_id}"):
                                delete_saved_task(task_id)
                        with cancel_col:
                            if st.button("취소", key=f"cancel-delete-{task_id}"):
                                st.session_state.pop("pending_delete_task_id", None)
                                st.rerun()
            if not items:
                st.info("저장된 이벤트 기획안이 없습니다. 이벤트를 생성한 뒤 새로고침해 주세요.")

with side_col:
    with st.container(key="panel-guide", border=True):
        card_title("◈", "참고 가이드")
        st.markdown(
            '<div class="ops-info"><strong>좋은 이벤트 요청을 위한 팁</strong>'
            "<ul><li>이벤트 대상과 목적을 구체적으로 적어주세요.</li>"
            "<li>원하는 성장·참여 방향을 목적에 설명해주세요.</li>"
            "<li>현재 생성 API는 기본 정보 네 항목을 받습니다.</li></ul></div>",
            unsafe_allow_html=True,
        )

    with st.container(key="panel-examples", border=True):
        card_title("▤", "이벤트 입력 예시")
        st.caption(
            "예시를 선택하면 입력칸이 채워집니다. 데모 실행은 실제 Backend를 호출하지 않습니다."
        )
        for index, example in enumerate(EXAMPLES):
            st.button(
                f"{example['event_name']} · {TARGET_LABELS[str(example['target_user'])]}  \n"
                f"{example['duration']}일 · {DEMO_SCENARIOS[index]}",
                key=f"example-{index}",
                width="stretch",
                on_click=load_example,
                args=(example, index),
            )
        selected_index = st.session_state.get("selected_example_index")
        if isinstance(selected_index, int) and 0 <= selected_index < len(EXAMPLES):
            st.caption(
                f"선택한 예시: {EXAMPLES[selected_index]['event_name']} · "
                f"{DEMO_SCENARIOS[selected_index]} 시나리오"
            )
        if st.button(
            "선택한 예시로 체험하기",
            key="run-demo",
            width="stretch",
            disabled=not isinstance(selected_index, int),
        ):
            run_demo()
        st.caption(
            "미리 준비한 기획안·검증·Trace를 표시합니다. 실제 AI 실행이나 승인 처리는 아닙니다."
        )

    with st.container(key="panel-data", border=True):
        card_title("▰", "Backend 연결 상태")
        st.caption("게임 데이터 건수와 MCP 개별 상태는 현재 API에서 제공하지 않습니다.")
        if st.button("연결 확인", key="ready-check", width="stretch"):
            try:
                ready = get_health("ready")
                st.session_state["ready_status"] = (
                    "준비됨" if ready.get("status") == "ready" else "응답 확인 필요"
                )
            except ApiError as exc:
                st.session_state["ready_status"] = f"연결 불가: {exc}"
        st.info(st.session_state.get("ready_status", "아직 확인하지 않았습니다."))
