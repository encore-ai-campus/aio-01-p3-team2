"""Replay recorded workflow steps as a small, read-only motion card."""

import json
from html import escape
from typing import Any
from uuid import uuid4

import streamlit as st

from components.trace_viewer import trace_items

PHASES = {
    "event_planner_agent": "이벤트 기획",
    "balance_rule_agent": "게임 규칙 검증",
    "critic_agent": "AI 최종 검수",
    "human": "운영자 승인",
}


def _steps(task: dict[str, Any], trace: dict[str, Any] | None) -> list[dict[str, str]]:
    """Use only Backend-reported execution records, in sequence order."""
    items = sorted(trace_items(trace), key=lambda item: item.get("sequence") or 0)
    result = []
    for item in items:
        actor = item.get("actor")
        action = str(item.get("action") or "")
        if actor == "supervisor" and action in {"RUN_AGENT", "RETRY", "TOOL_FALLBACK"}:
            label = "AutoHealing"
        else:
            label = PHASES.get(actor)
        if not label:
            continue
        result.append({"label": label, "status": str(item.get("status") or "UNKNOWN")})
    if result and task.get("workflow_status") == "WAITING_APPROVAL":
        result.append({"label": "운영자 승인", "status": "WAITING"})
    return result[:14]


def render_workflow_status(task: dict[str, Any], trace: dict[str, Any] | None) -> None:
    steps = _steps(task, trace)
    if not steps:
        st.info("Trace가 제공되면 실제 실행 순서를 이곳에서 재생합니다.")
        return

    root_id = f"motion-{uuid4().hex}"
    cards = "".join(
        '<div class="motion-step">'
        f'<span class="motion-index">{index}</span>'
        f"<strong>{escape(step['label'])}</strong>"
        f"<small>{escape(step['status'])}</small>"
        "</div>"
        for index, step in enumerate(steps, start=1)
    )
    data = json.dumps(steps, ensure_ascii=False).replace("</", "<\\/")
    markup = r"""
<style>
.motion-panel{font-family:inherit;border:1px solid #dce8f8;border-radius:10px;background:#fff;padding:18px 20px;color:#152649}
.motion-top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:14px}
.motion-top strong{font-size:16px}.motion-top small{color:#7385a5;font-size:12px}
.motion-replay{margin-left:auto;border:1px solid #cbdcf5;border-radius:7px;background:#f4f8ff;color:#2567cb;padding:6px 12px;font-weight:700;cursor:pointer}
.motion-track{height:5px;border-radius:8px;background:#edf3fc;overflow:hidden;margin-bottom:15px}
.motion-progress{height:100%;width:0;background:#2c80f6}
.motion-steps{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}
.motion-step{min-width:122px;flex:1;text-align:center;border:1px solid #e0e9f5;border-radius:8px;padding:10px 8px;opacity:.38;transition:none}
.motion-step.active{border-color:#8dbbfa;background:#eef6ff;opacity:1}
.motion-step.done{opacity:1}.motion-step strong,.motion-step small{display:block}
.motion-step strong{font-size:12px;white-space:nowrap}.motion-step small{margin-top:3px;color:#6480a9;font-size:10px}
.motion-index{display:grid;place-items:center;width:23px;height:23px;margin:0 auto 5px;border-radius:50%;background:#dceaff;color:#2671da;font-size:12px;font-weight:800}
.motion-step.done .motion-index{background:#2d81f4;color:#fff}
@media(prefers-reduced-motion:reduce){.motion-step{opacity:1}}
</style>
<div class="motion-panel" id="__ROOT__" role="group" aria-label="완료된 Workflow Trace 재생">
  <div class="motion-top"><strong>Workflow 실행 흐름</strong><small>완료된 Trace 재생 · 실시간 진행 표시 아님</small><button class="motion-replay" type="button">다시 보기</button></div>
  <div class="motion-track"><div class="motion-progress"></div></div>
  <div class="motion-steps">__CARDS__</div>
</div>
<script>
(() => {
  const root = document.getElementById("__ROOT__");
  if (!root) return;
  const steps = __DATA__;
  const cards = Array.from(root.querySelectorAll(".motion-step"));
  const progress = root.querySelector(".motion-progress");
  const duration = Math.max(1, steps.length * .62);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let start = null, frameId = 0;
  function seek(t) {
    const p = Math.max(0, Math.min(1, t / duration));
    const active = Math.min(cards.length - 1, Math.floor(p * cards.length));
    cards.forEach((card, index) => {
      card.classList.toggle("done", index < active || p === 1);
      card.classList.toggle("active", index === active && p < 1);
    });
    progress.style.width = `${p * 100}%`;
  }
  function tick(now) {
    if (start === null) start = now;
    const t = (now - start) / 1000;
    seek(t);
    if (t < duration) frameId = requestAnimationFrame(tick);
    else frameId = 0;
  }
  function play() {
    if (frameId) cancelAnimationFrame(frameId);
    start = null;
    seek(0);
    frameId = requestAnimationFrame(tick);
  }
  root.querySelector(".motion-replay").addEventListener("click", () => reduce ? seek(duration) : play());
  if (reduce) seek(duration);
  else play();
})();
</script>
"""
    markup = (
        markup.replace("__ROOT__", root_id).replace("__CARDS__", cards).replace("__DATA__", data)
    )
    st.html(markup, unsafe_allow_javascript=True)
