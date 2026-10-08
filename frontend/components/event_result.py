"""Read-only event plan and rule validation cards."""

from collections import defaultdict
from html import escape
from typing import Any

import streamlit as st

from components.event_form import TARGET_LABELS
from components.layout import card_title


def _text(value: Any) -> str:
    return escape(str(value)) if value is not None else "—"


def render_event_result(task: dict[str, Any]) -> None:
    result = task.get("final_result") or task.get("current_result")
    if not isinstance(result, dict):
        st.info("표시할 이벤트 기획안이 없습니다.")
        return

    with st.container(key="panel-result", border=True):
        card_title("▤", "AI 생성 이벤트 기획안")
        concept_html = (
            f'<div class="kv-item"><small>이벤트 콘셉트</small>{_text(result["event_concept"])}</div>'
            if result.get("event_concept")
            else ""
        )
        st.markdown(
            '<div class="kv-grid">'
            f'<div class="kv-item"><small>이벤트 대상</small>{_text(TARGET_LABELS.get(result.get("target_user"), result.get("target_user")))}</div>'
            f'<div class="kv-item"><small>이벤트 기간</small>{_text(result.get("duration"))}일</div>'
            f'<div class="kv-item"><small>이벤트 목적</small>{_text(result.get("objective"))}</div>'
            f"{concept_html}"
            "</div>",
            unsafe_allow_html=True,
        )
        if result.get("operation_summary"):
            st.caption(f"운영 방식: {result['operation_summary']}")

        duration = result.get("duration")
        if not isinstance(duration, int) or duration < 1:
            st.warning("이벤트 기간을 확인할 수 없습니다.")
            return
        missions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        rewards: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in result.get("missions") or []:
            if isinstance(item, dict) and isinstance(item.get("day"), int):
                missions[item["day"]].append(item)
        for item in result.get("rewards") or []:
            if isinstance(item, dict) and isinstance(item.get("day"), int):
                rewards[item["day"]].append(item)

        st.markdown("#### 이벤트 구성")
        for day in range(1, duration + 1):
            daily_missions = missions[day]
            daily_rewards = rewards[day]
            day_title = next(
                (m.get("day_title") for m in daily_missions if m.get("day_title")), None
            )
            mission_html = (
                "".join(
                    f"<p>{_text(m.get('mission_name'))}"
                    f"<small>{_text(m.get('content_name') or m.get('content_id'))}"
                    f" · 목표 {_text(m.get('target_count'))}</small></p>"
                    for m in daily_missions
                )
                or "<p>미션 데이터 없음</p>"
            )
            reward_html = (
                "".join(
                    f"<p>{_text(r.get('item_name') or r.get('item_id'))} × {_text(r.get('quantity'))}</p>"
                    for r in daily_rewards
                )
                or "<p>보상 데이터 없음</p>"
            )
            reasons = [m["planning_reason"] for m in daily_missions if m.get("planning_reason")]
            reason_html = (
                "<div><small>기획 의도</small><p>"
                + "<br>".join(_text(reason) for reason in reasons)
                + "</p></div>"
                if reasons
                else ""
            )
            st.markdown(
                '<div class="day-card">'
                f'<div class="day-number">{day}</div>'
                f"<div><small>Day {day}</small><strong>{_text(day_title or f'{day}일차 미션')}</strong></div>"
                f"<div><small>미션</small>{mission_html}</div>"
                f"<div><small>보상</small>{reward_html}</div>"
                f"{reason_html}"
                "</div>",
                unsafe_allow_html=True,
            )


def render_rule_validation(rule: dict[str, Any] | None) -> None:
    with st.container(key="panel-rules", border=True):
        card_title("⛨", "게임 규칙 검증")
        if not isinstance(rule, dict):
            st.info("규칙 검증 결과가 없습니다.")
            return
        if rule.get("is_valid") is True:
            st.success("규칙 검증 PASS")
        elif rule.get("is_valid") is False:
            st.error("규칙 검증 실패")
        else:
            st.info("규칙 검증 상태를 확인할 수 없습니다.")
        if rule.get("reason"):
            st.write(rule["reason"])
        if rule.get("error_type"):
            st.caption(f"Error Type: {rule['error_type']}")
        st.caption(
            f"데이터 버전: {rule.get('game_data_version') or '알 수 없음'} · "
            f"출처: {rule.get('data_source') or '알 수 없음'}"
        )
        evidence = rule.get("evidence")
        if isinstance(evidence, dict) and evidence:
            with st.expander("검증 근거 보기"):
                st.json(evidence)
        st.caption("개별 검사 PASS 목록은 현재 API에서 제공하지 않습니다.")
