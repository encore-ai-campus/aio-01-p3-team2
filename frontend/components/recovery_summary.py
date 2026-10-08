"""Summarize a recorded failure and its recovery outcome for operators."""

from html import escape
from typing import Any

import streamlit as st

from components.layout import card_title
from components.trace_viewer import ACTOR_LABELS, trace_items

ERROR_LABELS = {
    "mission_unreachable": "달성 불가능 미션",
    "mcp_timeout": "MCP 호출 시간 초과",
}
ACTION_LABELS = {
    "RUN_AGENT": "Agent 재실행",
    "RETRY": "기술 재시도",
    "TOOL_FALLBACK": "대체 Tool 사용",
    "SAFE_STOP": "안전 종료",
}


def _summary(task: dict[str, Any], trace: dict[str, Any] | None) -> tuple[str, list[str]]:
    healing = [event for event in task.get("autohealing_events") or [] if isinstance(event, dict)]
    failure = next(
        (
            item
            for item in trace_items(trace)
            if str(item.get("status") or "").upper() in {"ERROR", "FAILED"}
        ),
        None,
    )
    error = task.get("error") if isinstance(task.get("error"), dict) else {}
    error_type = (
        error.get("error_type")
        or (healing[0].get("error_type") if healing else None)
        or (failure.get("error_type") if failure else None)
    )
    error_label = ERROR_LABELS.get(str(error_type), str(error_type)) if error_type else "없음"
    failed_stage = (
        ACTOR_LABELS.get(failure.get("actor"), str(failure.get("actor")))
        if failure
        else "없음"
        if not error_type
        else "기록 없음"
    )

    if task.get("workflow_status") == "FAILED":
        retry_count = task.get("retry_count")
        attempts = (
            f"기술 재시도 {retry_count}회 → "
            if isinstance(retry_count, int) and retry_count
            else ""
        )
        action = f"{attempts}안전 종료"
        revalidation = "통과 기록 없음"
        outcome = "FAILED · 승인 불가"
        kind = "failed"
    elif healing:
        last = healing[-1]
        action_code = str(last.get("recovery_action") or "")
        action = ACTION_LABELS.get(action_code, action_code or "기록 없음")
        revalidation = str(last.get("revalidation_status") or "기록 없음")
        outcome = (
            "복구 후 승인 대기"
            if task.get("workflow_status") == "WAITING_APPROVAL"
            else str(task.get("workflow_status") or "상태 없음")
        )
        kind = "recovered"
    elif failure:
        action = "복구 결정 기록 없음"
        revalidation = "복구 재검증 기록 없음"
        outcome = str(task.get("workflow_status") or "상태 없음")
        kind = "unrecorded"
    else:
        action = "필요 없음"
        rule = task.get("rule_validation")
        revalidation = (
            "기본 검증 PASS"
            if isinstance(rule, dict) and rule.get("is_valid") is True
            else "검증 기록 없음"
        )
        outcome = (
            "정상 승인 대기"
            if task.get("workflow_status") == "WAITING_APPROVAL"
            else str(task.get("workflow_status") or "상태 없음")
        )
        kind = "normal"

    return kind, [error_label, failed_stage, action, revalidation, outcome]


def render_recovery_summary(task: dict[str, Any], trace: dict[str, Any] | None) -> None:
    kind, values = _summary(task, trace)
    with st.container(key="panel-recovery-summary", border=True):
        card_title("⛨", "장애 검증 요약")
        if kind == "failed":
            st.error("복구에 실패해 Workflow를 안전 종료했습니다. 승인할 수 없습니다.")
        elif kind == "recovered":
            st.success("오류 감지 후 복구 조치와 재검증 결과가 기록됐습니다.")
        elif kind == "unrecorded":
            st.warning("실패 Trace가 있지만 복구 결정 기록은 없습니다. 상세 Trace를 확인해주세요.")
        else:
            st.info("이번 실행에는 오류나 AutoHealing이 기록되지 않았습니다.")

        labels = ("오류 유형", "실패한 단계", "복구 조치", "재검증", "최종 상태")
        cards = "".join(
            '<div class="recovery-step">'
            f"<small>{escape(label)}</small><strong>{escape(str(value))}</strong>"
            "</div>"
            for label, value in zip(labels, values, strict=True)
        )
        st.markdown(
            "<style>"
            ".recovery-flow{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}"
            ".recovery-step{border:1px solid #dce8f8;border-radius:9px;padding:12px;"
            "background:#f8fbff;min-width:0}"
            ".recovery-step small{display:block;color:#617497;margin-bottom:6px}"
            ".recovery-step strong{display:block;color:#13284b;overflow-wrap:anywhere}"
            "@media(max-width:900px){.recovery-flow{grid-template-columns:repeat(2,minmax(0,1fr))}}"
            "</style>"
            f'<div class="recovery-flow">{cards}</div>',
            unsafe_allow_html=True,
        )
        if kind != "normal":
            st.page_link("pages/3_ops_debug.py", label="실패·복구 근거 Trace 보기", icon="🔎")
