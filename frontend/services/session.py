"""Small helpers for the Streamlit session shared by the three pages."""

from typing import Any

import streamlit as st

TASK_STATE_KEYS = (
    "task_id",
    "task_response",
    "trace_response",
    "workflow_status",
    "critic_status",
    "approval_status",
    "result_version",
    "current_result",
    "final_result",
    "rule_validation",
    "autohealing_events",
    "last_brief",
    "pending_create",
    "last_error",
    "demo_preview",
)


def clear_task() -> None:
    """Remove one workflow without clearing the demo login or form inputs."""
    for key in TASK_STATE_KEYS:
        st.session_state.pop(key, None)


def remember_task(task: dict[str, Any]) -> None:
    """Keep the Task Response available across pages and reruns."""
    previous = st.session_state.get("task_response")
    if isinstance(previous, dict) and previous.get("task_id") == task.get("task_id"):
        task = {**previous, **task}
    for state_key, response_key in (
        ("task_id", "task_id"),
        ("workflow_status", "workflow_status"),
        ("critic_status", "critic_status"),
        ("approval_status", "approval_status"),
        ("result_version", "result_version"),
        ("current_result", "current_result"),
        ("final_result", "final_result"),
        ("rule_validation", "rule_validation"),
        ("autohealing_events", "autohealing_events"),
    ):
        st.session_state[state_key] = task.get(response_key)
    st.session_state["demo_preview"] = task.get("_demo_preview") is True
    st.session_state["task_response"] = task
