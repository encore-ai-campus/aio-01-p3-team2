"""Show backend-reported AutoHealing events without inventing repair steps."""

from typing import Any

import streamlit as st

from components.layout import card_title


def render_autohealing(events: list[dict[str, Any]] | None) -> None:
    with st.container(key="panel-healing", border=True):
        card_title("⚙", "AutoHealing")
        if not events:
            st.success("이번 실행에는 AutoHealing이 발생하지 않았습니다.")
            return

        st.warning(f"AutoHealing {len(events)}회 기록")
        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                continue
            sequence = event.get("sequence", index)
            with st.expander(
                f"#{sequence} · {event.get('error_type') or '오류'} · "
                f"{event.get('revalidation_status') or '재검증 대기'}",
                expanded=index == 1,
            ):
                if event.get("reason"):
                    st.error(event["reason"])
                st.caption(
                    f"복구 조치: {event.get('recovery_action') or '정보 없음'} · "
                    f"대상: {event.get('target') or '정보 없음'}"
                )
                before, after = st.columns(2)
                with before:
                    st.markdown("**수정 전**")
                    if event.get("before") is not None:
                        st.json(event["before"])
                    else:
                        st.caption("비교 데이터 없음")
                with after:
                    st.markdown("**수정 후**")
                    if event.get("after") is not None:
                        st.json(event["after"])
                    else:
                        st.caption("비교 데이터 없음")
