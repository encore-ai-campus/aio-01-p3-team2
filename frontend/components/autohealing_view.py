"""Show backend-reported AutoHealing events without inventing repair steps."""

from typing import Any

import streamlit as st

from components.layout import card_title
from components.trace_viewer import trace_items

ACTION_LABELS = {
    "RUN_AGENT": "Agent 재실행",
    "RETRY": "기술 재시도",
    "TOOL_FALLBACK": "대체 Tool 사용",
    "SAFE_STOP": "복구 실패 후 안전 종료",
}


def _render_change(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        st.caption("비교 데이터가 없습니다. 실행 시도는 상세 Trace에서 확인해주세요.")
        return
    if value.get("day") is not None:
        st.write(f"**날짜:** Day {value['day']}")
    if value.get("mission_name"):
        st.write(f"**미션:** {value['mission_name']}")
    content = value.get("content_name") or value.get("content_id")
    if content:
        st.write(f"**콘텐츠:** {content}")
    if value.get("required_level") is not None:
        st.write(f"**필요 레벨:** Lv.{value['required_level']}")
    missions = value.get("missions")
    if isinstance(missions, list):
        for mission in missions:
            if isinstance(mission, dict):
                st.write(
                    f"**Day {mission.get('day', '?')} 미션:** "
                    f"{mission.get('content_id') or '알 수 없음'} · "
                    f"목표 {mission.get('target_count', '?')}회"
                )
    rewards = value.get("rewards")
    if isinstance(rewards, list):
        for reward in rewards:
            if isinstance(reward, dict):
                st.write(
                    f"**Day {reward.get('day', '?')} 보상:** "
                    f"{reward.get('item_id') or '알 수 없음'} · "
                    f"수량 {reward.get('quantity', '?')}"
                )
    with st.expander("전체 비교 데이터 보기"):
        st.json(value)


def _change_summary(before: Any, after: Any) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    labels = {
        "content_name": "콘텐츠",
        "mission_name": "미션",
        "required_level": "필요 레벨",
    }
    changes = []
    for key, label in labels.items():
        old, new = before.get(key), after.get(key)
        if old is None or new is None or old == new:
            continue
        if key == "required_level":
            old, new = f"Lv.{old}", f"Lv.{new}"
        changes.append(f"{label}: {old} → {new}")
    for group, fields in (
        ("missions", {"content_id": "미션", "target_count": "목표 횟수"}),
        ("rewards", {"item_id": "보상", "quantity": "보상 수량"}),
    ):
        old_rows, new_rows = before.get(group), after.get(group)
        if not isinstance(old_rows, list) or not isinstance(new_rows, list):
            continue
        old_by_day = {row.get("day"): row for row in old_rows if isinstance(row, dict)}
        for row in new_rows:
            if not isinstance(row, dict):
                continue
            day = row.get("day")
            old_row = old_by_day.get(day)
            if not isinstance(old_row, dict):
                continue
            for key, label in fields.items():
                old, new = old_row.get(key), row.get(key)
                if old is not None and new is not None and old != new:
                    changes.append(f"Day {day} {label}: {old} → {new}")
    return changes


def render_autohealing(
    events: list[dict[str, Any]] | None,
    rule_validation: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
) -> None:
    with st.container(key="panel-healing", border=True):
        card_title("⚙", "AutoHealing")
        valid_events = [event for event in events or [] if isinstance(event, dict)]
        if not valid_events:
            failed_trace = any(
                str(item.get("status") or "").upper() in {"ERROR", "FAILED"}
                for item in trace_items(trace)
            )
            if failed_trace:
                st.warning("실패 Trace가 있지만 AutoHealing 조치 기록은 없습니다.")
            else:
                st.info(
                    "이번 실행에는 복구가 필요한 오류가 없어 AutoHealing이 실행되지 않았습니다."
                )
            return

        safe_stop = any(event.get("recovery_action") == "SAFE_STOP" for event in valid_events)
        if safe_stop:
            st.warning(f"복구 실패·안전 종료 {len(valid_events)}건 기록")
        else:
            st.info(f"AutoHealing {len(valid_events)}회 기록")
        for index, event in enumerate(valid_events, start=1):
            sequence = event.get("sequence", index)
            status = str(event.get("revalidation_status") or "").upper()
            status_label = "미실시" if status == "NOT_RUN" else status or "기록 없음"
            with st.expander(
                f"#{sequence} · {event.get('error_type') or '오류'} · {status_label}",
                expanded=index == 1,
            ):
                st.markdown("**왜 실패했나**")
                if event.get("reason"):
                    st.error(str(event["reason"]))
                else:
                    st.caption("실패 이유가 기록되지 않았습니다. 상세 Trace를 확인해주세요.")
                st.caption(f"오류 유형: {event.get('error_type') or '기록 없음'}")

                st.markdown("**어떻게 대응했나**")
                action_code = str(event.get("recovery_action") or "")
                st.info(
                    f"복구 조치: {ACTION_LABELS.get(action_code, action_code or '정보 없음')} · "
                    f"대상: {event.get('target') or '정보 없음'}"
                )
                if event.get("attempts") is not None:
                    st.caption(f"기술 재시도: {event['attempts']}회")
                for change in _change_summary(event.get("before"), event.get("after")):
                    st.write(f"- {change}")
                if event.get("before") is not None or event.get("after") is not None:
                    before, after = st.columns(2)
                    with before:
                        st.markdown("#### 수정 전")
                        _render_change(event.get("before"))
                    with after:
                        st.markdown("#### 수정 후")
                        _render_change(event.get("after"))
                else:
                    st.caption(
                        "수정 전후 비교는 없습니다. 기술 재시도 과정은 상세 Trace에 기록됩니다."
                    )

                st.markdown("**재검증 결과와 이유**")
                if status == "PASS":
                    st.success("재검증 PASS로 기록됐습니다.")
                    final_rule = (
                        rule_validation
                        if index == len(valid_events) and isinstance(rule_validation, dict)
                        else None
                    )
                    if final_rule and final_rule.get("is_valid") is True:
                        if final_rule.get("reason"):
                            st.write(str(final_rule["reason"]))
                        else:
                            st.caption("최종 규칙 검증의 상세 성공 이유가 제공되지 않았습니다.")
                        evidence = final_rule.get("evidence")
                        if isinstance(evidence, dict) and evidence:
                            expected = evidence.get("expected_level")
                            required = evidence.get("required_level")
                            if expected is not None and required is not None:
                                st.caption(
                                    f"검증 수치: 대상 유저 예상 Lv.{expected} · "
                                    f"수정 후 콘텐츠 필요 Lv.{required}"
                                )
                            with st.expander("재검증 근거 데이터 보기"):
                                st.json(evidence)
                    else:
                        st.caption("PASS 기록은 있으나 상세 성공 근거는 제공되지 않았습니다.")
                elif status in {"ERROR", "FAILED"}:
                    st.error("재검증 실패 · Workflow 최종 상태를 확인해주세요.")
                elif status == "NOT_RUN":
                    st.warning("재검증 미실시 · 통과 여부를 확인할 수 없습니다.")
                else:
                    st.caption("재검증 결과가 기록되지 않았습니다.")
