"""Trace-first operations view for Agent, MCP, and Backend execution."""

from datetime import datetime
from typing import Any

import streamlit as st

from components.layout import card_title

ACTOR_LABELS = {
    "input_guard": "Input Guard",
    "supervisor": "Supervisor",
    "event_planner_agent": "Event Planner",
    "balance_rule_agent": "Balance & Rule",
    "critic_agent": "Critic",
    "human": "Human Approval",
    "backend": "Backend",
    "mcp": "MCP Tool",
    "database": "Database",
}


def trace_items(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    value = response.get("traces") if isinstance(response, dict) else None
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def display_time(value: Any) -> str:
    if not isinstance(value, str):
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%H:%M:%S")
    except ValueError:
        return value


def _stage(item: dict[str, Any], *, after_failure: bool) -> str:
    actor = item.get("actor")
    action = item.get("action")
    phase = item.get("phase")
    if actor == "mcp":
        if phase == "planning_context" or action == "get_event_planning_context":
            return "기획용 게임 데이터 조회"
        if phase == "revalidation":
            return "재검증용 게임 데이터 조회"
        return "검증용 게임 데이터 조회"
    if actor == "supervisor":
        if phase == "routing":
            return "운영자 승인 대기 결정" if action == "WAIT_APPROVAL" else "다음 단계 결정"
        return {
            "RUN_AGENT": "AutoHealing · Agent 재실행",
            "RETRY": "기술 재시도",
            "TOOL_FALLBACK": "Fallback Tool 전환",
            "SAFE_STOP": "복구 실패 · 안전 종료",
        }.get(action, "Supervisor 결정")
    if (
        (after_failure or phase == "revalidation")
        and actor == "balance_rule_agent"
        and action == "validate_plan"
    ):
        return "복구 후 재검증"
    return ACTOR_LABELS.get(actor, str(actor or "알 수 없음"))


def render_trace(
    response: dict[str, Any] | None,
    *,
    actors: set[str] | None = None,
    title: str = "상세 Trace",
    key: str = "panel-trace",
) -> None:
    items = sorted(trace_items(response), key=lambda item: item.get("sequence") or 0)
    if actors is not None:
        items = [item for item in items if item.get("actor") in actors]
    with st.container(key=key, border=True):
        card_title("▤", title)
        if not items:
            st.info("표시할 Trace가 없습니다. Backend가 실행 기록을 제공하면 여기에 나타납니다.")
            return

        rows = []
        highlights = []
        after_failure = False
        for item in items:
            status = str(item.get("status") or "").upper()
            stage = _stage(item, after_failure=after_failure)
            summary = str(item.get("reason") or item.get("error_type") or "—")
            rows.append(
                {
                    "순서": item.get("sequence"),
                    "시간": display_time(item.get("time")),
                    "단계": stage,
                    "실행 주체": ACTOR_LABELS.get(item.get("actor"), item.get("actor")),
                    "Action": item.get("action"),
                    "상태": status,
                    "요약": summary,
                }
            )
            if (
                status in {"ERROR", "FAILED"}
                or item.get("actor") == "supervisor"
                or (
                    after_failure
                    and item.get("actor") == "balance_rule_agent"
                    and status == "SUCCESS"
                )
            ):
                highlights.append((item.get("sequence"), stage, status, summary))
            if status in {"ERROR", "FAILED"}:
                after_failure = True

        if highlights:
            st.markdown("#### 주요 경로·실패·복구 기록")
            for sequence, stage, status, summary in highlights:
                message = f"#{sequence} · {stage} · {status} · {summary}"
                if status in {"ERROR", "FAILED"}:
                    st.error(message)
                elif stage == "복구 후 재검증":
                    st.success(message)
                else:
                    st.info(message)
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("행별 전체 필드와 Retry·Fallback 근거는 아래에서 확인할 수 있습니다.")
        for item in items:
            label = (
                f"#{item.get('sequence', '?')} · {display_time(item.get('time'))} · "
                f"{ACTOR_LABELS.get(item.get('actor'), item.get('actor', '알 수 없음'))} · "
                f"{item.get('action') or 'Action 없음'} · {item.get('status') or '상태 없음'}"
            )
            with st.expander(label):
                st.json(item)
