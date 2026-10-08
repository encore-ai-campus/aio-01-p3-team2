"""Create a game event from the documented MVP brief."""

import json
from uuid import uuid4

import streamlit as st

from components.event_form import TARGET_LABELS, render_event_form
from components.layout import card_title, setup_page
from services.backend_client import ApiError, generate_event, get_health
from services.demo_data import build_demo_result
from services.session import remember_task

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


def load_example(example: dict[str, str | int], index: int) -> None:
    """Fill only the four fields actually supported by the generation API."""
    st.session_state["brief-name"] = example["event_name"]
    st.session_state["brief-target"] = example["target_user"]
    st.session_state["brief-duration"] = example["duration"]
    st.session_state["brief-objective"] = example["objective"]
    st.session_state["selected_example_index"] = index


def run_demo() -> None:
    """Open a labeled sample result without making a Backend request."""
    index = st.session_state.get("selected_example_index")
    if not isinstance(index, int) or not 0 <= index < len(EXAMPLES):
        st.warning("먼저 예시를 선택해주세요.")
        return
    example = dict(EXAMPLES[index])
    task, trace = build_demo_result(example)
    st.session_state.pop("pending_create", None)
    st.session_state.pop("last_error", None)
    st.session_state["last_brief"] = example
    st.session_state["trace_response"] = trace
    remember_task(task)
    st.switch_page("pages/2_event_result.py")


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

    with st.spinner("AI가 이벤트를 생성하고 규칙을 검증하고 있습니다. 완료까지 기다려주세요."):
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
    remember_task(task)
    st.switch_page("pages/2_event_result.py")


setup_page("이벤트 생성", "AI가 게임 데이터 기반으로 이벤트를 자동 기획합니다.")

main_col, side_col = st.columns([2.25, 1], gap="medium")
with main_col:
    brief = render_event_form()
    st.markdown(
        '<p class="muted-note">게임 데이터 기반 이벤트 기획 → 규칙 검증 → AutoHealing → 최종 검수 순서로 진행됩니다.</p>',
        unsafe_allow_html=True,
    )
    if brief is not None:
        create_event(brief)

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
                f"{example['event_name']}  ·  {TARGET_LABELS[str(example['target_user'])]} / {example['duration']}일  ·  {DEMO_SCENARIOS[index]}",
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
