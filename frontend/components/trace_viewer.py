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


def render_trace(
    response: dict[str, Any] | None,
    *,
    actors: set[str] | None = None,
    title: str = "상세 Trace",
    key: str = "panel-trace",
) -> None:
    items = trace_items(response)
    if actors is not None:
        items = [item for item in items if item.get("actor") in actors]
    with st.container(key=key, border=True):
        card_title("▤", title)
        if not items:
            st.info("표시할 Trace가 없습니다. Backend가 실행 기록을 제공하면 여기에 나타납니다.")
            return

        rows = [
            {
                "순서": item.get("sequence"),
                "시간": display_time(item.get("time")),
                "실행 주체": ACTOR_LABELS.get(item.get("actor"), item.get("actor")),
                "Action": item.get("action"),
                "상태": item.get("status"),
                "요약": item.get("reason") or item.get("error_type") or "—",
            }
            for item in items
        ]
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
