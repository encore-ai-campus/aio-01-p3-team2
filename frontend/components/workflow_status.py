"""Replay recorded workflow steps on a branching, read-only motion map."""

import json
from html import escape
from typing import Any
from uuid import uuid4

import streamlit as st

from components.trace_viewer import trace_items

PHASES = {
    "event_planner_agent": ("이벤트 기획", "planner"),
    "balance_rule_agent": ("게임 데이터 비교·검증", ""),
    "critic_agent": ("이벤트 기획안 종합 검토", ""),
    "human": ("운영자 승인", "approval"),
}

ACTOR_LABELS = {
    "event_planner_agent": "Event Planner Agent",
    "balance_rule_agent": "Balance & Rule Agent",
    "critic_agent": "Critic Agent",
    "supervisor": "Supervisor Agent",
    "mcp": "MCP Server",
    "human": "운영 담당자",
}

NODES = (
    ("planning_data", "MCP 기획 데이터 조회", "MCP Server"),
    ("planner", "이벤트 기획", "진행 단계"),
    ("validation_data", "MCP 검증 데이터 조회", "MCP Server"),
    ("approval", "운영자 승인", "운영 담당자"),
    ("planner_agent", "Event Planner Agent", "이벤트 기획·수정"),
    ("balance_agent", "Balance & Rule Agent", "게임 데이터 비교·검증"),
    ("critic_agent", "Critic Agent", "이벤트 기획안 종합 검토"),
    ("supervisor_agent", "Supervisor Agent", "다음 단계 선택·오류 복구"),
)

AGENT_NODES = {
    "event_planner_agent": "planner_agent",
    "balance_rule_agent": "balance_agent",
    "critic_agent": "critic_agent",
    "supervisor": "supervisor_agent",
}
ROUTE_TARGET_LABELS = {
    "event_planner_agent": "Event Planner",
    "balance_rule_agent": "Balance & Rule",
    "critic_agent": "Critic",
}


def _steps(task: dict[str, Any], trace: dict[str, Any] | None) -> list[dict[str, str]]:
    """Use only Backend-reported execution records, in sequence order."""
    items = sorted(trace_items(trace), key=lambda item: item.get("sequence") or 0)
    result = []
    recovery_seen = False
    for item in items:
        actor = item.get("actor")
        action = str(item.get("action") or "")
        phase = str(item.get("phase") or "")
        status = str(item.get("status") or "UNKNOWN").upper()
        if actor == "mcp":
            if phase == "planning_context" or action == "get_event_planning_context":
                label, node = "기획용 게임 데이터 조회", "planning_data"
            else:
                label = (
                    "재검증용 게임 데이터 조회"
                    if phase == "revalidation"
                    else "검증용 게임 데이터 조회"
                )
                node = "validation_data"
        elif actor == "supervisor":
            node = ""
            if phase == "routing":
                target = ROUTE_TARGET_LABELS.get(str(item.get("next_agent") or ""))
                label = (
                    "운영자 승인 대기 결정"
                    if action == "WAIT_APPROVAL"
                    else f"{target}로 진행 결정"
                    if action == "RUN_AGENT" and target
                    else "다음 단계 결정"
                )
            else:
                label = {
                    "RUN_AGENT": "AutoHealing · Agent 재실행",
                    "RETRY": "기술 재시도",
                    "TOOL_FALLBACK": "대체 Tool 전환",
                    "SAFE_STOP": "안전 종료",
                }.get(action)
                if label:
                    recovery_seen = True
        elif actor == "event_planner_agent" and action == "regenerate_plan":
            label, node = "이벤트 재기획", "planner"
        elif actor == "balance_rule_agent" and action == "validate_plan" and recovery_seen:
            label, node = "복구 후 재검증", ""
        else:
            label, node = PHASES.get(actor, (None, None))
        if not label:
            continue
        tone = (
            "failed"
            if status in {"ERROR", "FAILED"}
            else "waiting"
            if status in {"WAITING", "PENDING"}
            else "recovery"
            if (actor == "supervisor" and phase != "routing") or action == "regenerate_plan"
            else "pass"
        )
        result.append(
            {
                "label": label,
                "node": node,
                "agent_node": AGENT_NODES.get(str(actor), ""),
                "actor": str(actor or ""),
                "actor_label": ACTOR_LABELS.get(actor, str(actor or "알 수 없음")),
                "status": status,
                "reason": str(item.get("reason") or item.get("error_type") or ""),
                "action": action,
                "sequence": str(item.get("sequence") or ""),
                "tone": tone,
            }
        )
        if status in {"ERROR", "FAILED"}:
            recovery_seen = True
    if (
        result
        and task.get("workflow_status") == "WAITING_APPROVAL"
        and not any(step["node"] == "approval" for step in result)
    ):
        result.append(
            {
                "label": "운영자 승인",
                "node": "approval",
                "agent_node": "",
                "actor": "",
                "actor_label": "운영 담당자 · 대기",
                "status": "WAITING",
                "reason": "Task가 운영자 결정을 기다리는 상태입니다.",
                "action": "",
                "sequence": "",
                "tone": "waiting",
            }
        )
    return result


def render_workflow_status(task: dict[str, Any], trace: dict[str, Any] | None) -> None:
    steps = _steps(task, trace)
    if not steps:
        st.info("Trace가 제공되면 실제 실행 순서를 이곳에서 재생합니다.")
        return

    root_id = f"workflow-motion-{uuid4().hex}"
    nodes = "".join(
        f'<div class="flow-node" data-node="{node}" role="listitem" '
        f'data-lane="{escape(lane)}" '
        f'aria-label="{escape(label)}">'
        f'<span class="flow-kicker">{escape(lane)}</span>'
        f"<strong>{escape(label)}</strong>"
        '<span class="flow-state">미실행</span><span class="flow-count" hidden></span></div>'
        for node, label, lane in NODES
    )
    data = json.dumps(steps, ensure_ascii=False).replace("<", "\\u003c")
    markup = r"""
<style>
.motion-panel{font-family:inherit;border:1px solid #dce8f8;border-radius:14px;background:#fff;padding:18px 20px;color:#152649;box-shadow:0 10px 28px rgba(31,75,138,.06)}
.motion-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.motion-heading{display:flex;flex-direction:column;gap:2px}.motion-heading strong{font-size:17px}.motion-heading small{color:#7183a2;font-size:12px}
.motion-controls{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.motion-controls button{border:1px solid #cbdcf5;border-radius:8px;background:#f4f8ff;color:#2567cb;padding:7px 10px;font-weight:700;font-size:12px;cursor:pointer}
.motion-controls button:hover{background:#e6f1ff}.motion-controls button:focus-visible{outline:3px solid #70a8ff;outline-offset:2px}
.motion-map-scroll{overflow-x:auto;border-radius:12px}
.flow-map{position:relative;min-width:1050px;height:350px;overflow:hidden;border:1px solid #dce8f8;border-radius:12px;background:radial-gradient(circle at 45% 42%,#eef6ff 0,transparent 44%),linear-gradient(145deg,#f9fcff,#f2f7fe)}
.flow-map::after{content:"";position:absolute;left:0;right:0;top:50%;border-top:1px dashed #d8e5f7;pointer-events:none}
.flow-lines{position:absolute;inset:0;width:100%;height:100%;z-index:1;overflow:visible}
.flow-route{fill:none;stroke:#75a9f5;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}.flow-route.tone-failed{stroke:#ed6675}.flow-route.tone-recovery{stroke:#eeb955}
.flow-route.current{stroke-width:5;filter:drop-shadow(0 0 5px #75a9f5);stroke-dasharray:12 9;animation:flow-route 1s linear infinite}.flow-route.current.tone-failed{filter:drop-shadow(0 0 5px #ed6675)}.flow-route.current.tone-recovery{filter:drop-shadow(0 0 5px #eeb955)}
.flow-node{position:absolute;z-index:2;width:14.5%;height:82px;box-sizing:border-box;padding:10px 12px;border:1px solid #d5e2f3;border-radius:11px;background:rgba(255,255,255,.94);box-shadow:0 5px 12px rgba(31,75,138,.05);opacity:.57;transition:transform .3s,box-shadow .3s,border-color .3s,opacity .3s}
.flow-node[data-node="planning_data"]{left:1.5%;top:12%}.flow-node[data-node="planner"]{left:27%;top:12%}.flow-node[data-node="validation_data"]{left:52.5%;top:12%}.flow-node[data-node="approval"]{left:78%;top:12%}
.flow-node[data-lane="진행 단계"],.flow-node[data-lane="MCP Server"],.flow-node[data-node="approval"]{width:20.5%}
.flow-node[data-node="planner_agent"]{left:2.5%;top:60%}.flow-node[data-node="balance_agent"]{left:27.5%;top:60%}.flow-node[data-node="critic_agent"]{left:52.5%;top:60%}.flow-node[data-node="supervisor_agent"]{left:77.5%;top:60%}
.flow-node[data-node$="_agent"]{width:20%}.flow-node[data-node$="_agent"] strong{font-size:12px}
.flow-kicker{display:block;font-size:10px;color:#7388a9;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.flow-node.visited .flow-kicker{color:#225eae;font-weight:800}.flow-node strong{display:block;font-size:12px;line-height:1.2}.flow-state{display:inline-block;margin-top:5px;font-size:10px;font-weight:800;color:#8292aa}
.flow-count{position:absolute;right:9px;bottom:8px;font-size:10px;font-weight:700;color:#6d85a5}.flow-node.visited{opacity:1}
.flow-node.tone-pass{border-color:#9bdabc;background:#f3fff8}.flow-node.tone-pass .flow-state{color:#087d44}
.flow-node.tone-failed{border-color:#f1aab2;background:#fff1f3}.flow-node.tone-failed .flow-state{color:#ca3040}
.flow-node.tone-recovery{border-color:#edca79;background:#fff9e9}.flow-node.tone-recovery .flow-state{color:#966100}
.flow-node.tone-waiting{border-color:#b8cae8;background:#f5f8ff}.flow-node.tone-waiting .flow-state{color:#4b69a2}
.flow-node.current{transform:scale(1.065);box-shadow:0 0 0 4px rgba(45,129,244,.15),0 12px 26px rgba(31,75,138,.15)}
.flow-node.current.tone-failed{box-shadow:0 0 0 4px rgba(237,102,117,.18),0 12px 26px rgba(138,31,52,.13)}
.flow-node.current.tone-recovery{box-shadow:0 0 0 4px rgba(238,185,85,.22),0 12px 26px rgba(138,90,31,.13)}
.motion-detail{display:flex;align-items:center;gap:16px;margin-top:14px;padding:14px 16px;border:1px solid #dce8f8;border-radius:11px;background:#f8fbff;min-height:82px}
.motion-detail.tone-failed{border-color:#f1b7bf;background:#fff5f6}.motion-detail.tone-recovery{border-color:#efd393;background:#fffaf0}.motion-detail.tone-pass{border-color:#b9e6cd;background:#f7fffa}
.motion-badge{flex:none;display:grid;place-items:center;width:50px;height:50px;border-radius:15px;background:#e5f0ff;color:#226fe3;font-weight:900;font-size:15px}
.motion-detail.tone-failed .motion-badge{background:#ffe1e5;color:#c72e43}.motion-detail.tone-recovery .motion-badge{background:#ffefc9;color:#936100}.motion-detail.tone-pass .motion-badge{background:#def9e8;color:#087d44}
.motion-detail-main{min-width:0;flex:1}.motion-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:16px;font-weight:800}
.motion-actor-line{display:flex;align-items:center;gap:7px;margin-top:5px;font-size:11px;color:#6a7f9f}.motion-actor{font-weight:800;color:#254a82}
.motion-status{border-radius:999px;padding:2px 8px;background:#e7efff;color:#3264a8;font-size:10px;font-weight:900}.motion-detail.tone-failed .motion-status{background:#ffe0e5;color:#bf2840}.motion-detail.tone-recovery .motion-status{background:#ffedc5;color:#865a00}.motion-detail.tone-pass .motion-status{background:#def9e8;color:#087d44}
.motion-reason{margin:4px 0 0;color:#536886;font-size:12px;line-height:1.5;overflow-wrap:anywhere}.motion-meta{flex:none;max-width:185px;color:#7284a2;font-size:11px;text-align:right;overflow-wrap:anywhere}
.motion-footer{display:flex;align-items:center;gap:12px;margin-top:13px;color:#6d809e;font-size:11px}.motion-track{flex:1;height:5px;border-radius:9px;background:#e9f1fb;overflow:hidden}.motion-progress{height:100%;width:0;background:linear-gradient(90deg,#2d81f4,#68b8ff);transition:width .12s linear}
@keyframes flow-route{to{stroke-dashoffset:-21}}
@media(max-width:700px){.motion-panel{padding:14px}.motion-controls{margin-left:0}.motion-detail{align-items:flex-start}.motion-meta{display:none}}
@media(prefers-reduced-motion:reduce){.flow-node,.motion-progress{transition:none!important}.flow-route.current{animation:none}.flow-node.current{transform:none}}
</style>
<div class="motion-panel" id="__ROOT__" role="group" aria-label="완료된 Workflow Trace 재생">
  <div class="motion-top">
    <div class="motion-heading"><strong>Workflow 분기 지도</strong><small>완료된 Trace 재생 · 현재 재생 위치 표시 · 실시간 진행 아님</small></div>
    <div class="motion-controls" aria-label="Trace 재생 조작">
      <button type="button" data-control="prev" aria-label="이전 기록">이전</button>
      <button type="button" data-control="play" aria-label="재생 일시정지">일시정지</button>
      <button type="button" data-control="next" aria-label="다음 기록">다음</button>
      <button type="button" data-control="replay" aria-label="처음부터 다시 보기">다시 보기</button>
    </div>
  </div>
  <div class="motion-map-scroll"><div class="flow-map" role="list" aria-label="MCP 조회 단계 2개와 진행 단계 2개, 담당 에이전트 4개의 실행 경로">
    __NODES__
  </div></div>
  <div class="motion-detail" aria-live="polite" aria-atomic="true">
    <span class="motion-badge" aria-hidden="true"></span>
    <div class="motion-detail-main"><div class="motion-title"><span class="motion-stage"></span><span class="motion-status"></span></div><div class="motion-actor-line">실행 주체 <strong class="motion-actor"></strong></div><p class="motion-reason"></p></div>
    <div class="motion-meta"></div>
  </div>
  <div class="motion-footer"><span class="motion-position"></span><div class="motion-track"><div class="motion-progress"></div></div><span>Trace 기록 재생</span></div>
</div>
<script>
(() => {
  const root = document.getElementById("__ROOT__");
  if (!root) return;
  const steps = __DATA__;
  const nodes = new Map(Array.from(root.querySelectorAll(".flow-node")).map(node => [node.dataset.node, node]));
  const map = root.querySelector(".flow-map");
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  svg.classList.add("flow-lines");
  svg.setAttribute("aria-hidden", "true");
  const defs = document.createElementNS(svgNs, "defs");
  for (const [color, fill] of Object.entries({blue:"#2d81f4",red:"#e34c60",gold:"#d79a19"})) {
    const marker = document.createElementNS(svgNs, "marker");
    marker.id = `${root.id}-arrow-${color}`;
    for (const [name, value] of Object.entries({viewBox:"0 0 14 14",refX:"11",refY:"7",markerWidth:"14",markerHeight:"14",markerUnits:"userSpaceOnUse",orient:"auto"})) marker.setAttribute(name, value);
    const tip = document.createElementNS(svgNs, "path");
    tip.setAttribute("d", "M1 1 L11 7 L1 13 Z");
    tip.setAttribute("fill", fill);
    marker.appendChild(tip);
    defs.appendChild(marker);
  }
  const routeLayer = document.createElementNS(svgNs, "g");
  svg.append(defs, routeLayer);
  map.prepend(svg);
  const detail = root.querySelector(".motion-detail");
  const progress = root.querySelector(".motion-progress");
  const playButton = root.querySelector('[data-control="play"]');
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const stepSeconds = 1.15;
  const duration = steps.length * stepSeconds;
  const statusLabels = {SUCCESS:"성공",PASS:"통과",ERROR:"오류",FAILED:"실패",WAITING:"대기",PENDING:"대기"};
  const hasPlanningRecord = steps.some(step => step.node === "planning_data");
  const hasValidationRecord = steps.some(step => step.node === "validation_data");
  let position = reduce ? duration : 0;
  let playing = !reduce;
  let visible = true;
  let frameId = 0;
  let lastFrame = null;
  const routeKey = (a, b) => `${a}:${b}`;

  function routePath(from, to) {
    const bounds = map.getBoundingClientRect();
    const first = nodes.get(from).getBoundingClientRect();
    const second = nodes.get(to).getBoundingClientRect();
    const x1 = first.left - bounds.left + first.width / 2;
    const x2 = second.left - bounds.left + second.width / 2;
    const y1 = first.top - bounds.top + first.height / 2;
    const y2 = second.top - bounds.top + second.height / 2;
    const sameRow = Math.abs(y1 - y2) < 20;
    if (sameRow && from.endsWith("_agent") && to.endsWith("_agent") && Math.abs(x1 - x2) > first.width * 1.5) {
      const under = map.clientHeight - 25;
      return `M ${x1} ${first.bottom - bounds.top + 2} V ${under} H ${x2} V ${second.bottom - bounds.top + 8}`;
    }
    if (sameRow) {
      const right = x2 > x1;
      const start = right ? first.right - bounds.left + 2 : first.left - bounds.left - 2;
      const end = right ? second.left - bounds.left - 8 : second.right - bounds.left + 8;
      return `M ${start} ${y1} H ${end}`;
    }
    const downward = y2 > y1;
    const startY = downward ? first.bottom - bounds.top + 2 : first.top - bounds.top - 2;
    const endY = downward ? second.top - bounds.top - 8 : second.bottom - bounds.top + 8;
    const middle = (startY + endY) / 2;
    return `M ${x1} ${startY} V ${middle} H ${x2} V ${endY}`;
  }

  function drawRoutes(traversed, currentIndex, finished) {
    svg.setAttribute("viewBox", `0 0 ${map.clientWidth} ${map.clientHeight}`);
    routeLayer.replaceChildren();
    for (const visit of traversed.values()) {
      const route = document.createElementNS(svgNs, "path");
      route.classList.add("flow-route");
      if (visit.step.tone === "failed" || visit.step.tone === "recovery") route.classList.add(`tone-${visit.step.tone}`);
      if (!finished && visit.index === currentIndex) route.classList.add("current");
      route.setAttribute("d", routePath(visit.from, visit.to));
      route.setAttribute("data-route", `${visit.from}:${visit.to}`);
      const color = visit.step.tone === "failed" ? "red" : visit.step.tone === "recovery" ? "gold" : "blue";
      route.setAttribute("marker-end", `url(#${root.id}-arrow-${color})`);
      routeLayer.appendChild(route);
    }
  }

  function seek(t) {
    position = Math.max(0, Math.min(duration, t));
    const index = Math.min(steps.length - 1, Math.floor(position / stepSeconds));
    const finished = position >= duration;
    const current = steps[index];
    const visits = new Map();
    const traversed = new Map();
    let previousNode = "";
    for (let i = 0; i <= index; i++) {
      const step = steps[i];
      for (const node of [step.agent_node, step.node].filter(Boolean)) {
        const record = visits.get(node) || {count:0, step};
        record.count += 1;
        record.step = step;
        visits.set(node, record);
        if (previousNode && previousNode !== node) {
          traversed.set(routeKey(previousNode, node), {
            step, index:i, from:previousNode, to:node
          });
        }
        previousNode = node;
      }
    }
    nodes.forEach((node, key) => {
      const visit = visits.get(key);
      node.classList.remove("visited", "current", "tone-pass", "tone-failed", "tone-recovery", "tone-waiting");
      node.querySelector(".flow-state").textContent = visit ? statusLabels[visit.step.status] || visit.step.status : (key === "planning_data" && !hasPlanningRecord) || (key === "validation_data" && !hasValidationRecord) ? "Trace 기록 없음" : "미실행";
      const count = node.querySelector(".flow-count");
      count.hidden = !visit || visit.count < 2;
      count.textContent = visit ? `${visit.count}회` : "";
      if (visit) node.classList.add("visited", `tone-${visit.step.tone}`);
      if (!finished && (current.node === key || current.agent_node === key)) node.classList.add("current");
    });
    drawRoutes(traversed, index, finished);
    detail.className = `motion-detail tone-${current.tone}`;
    root.querySelector(".motion-badge").textContent = `${index + 1}/${steps.length}`;
    root.querySelector(".motion-stage").textContent = current.label;
    root.querySelector(".motion-status").textContent = `${statusLabels[current.status] || current.status} · ${current.status}`;
    root.querySelector(".motion-actor").textContent = current.actor_label;
    root.querySelector(".motion-reason").textContent = current.reason || "Trace에 상세 사유가 없습니다.";
    root.querySelector(".motion-meta").textContent = [current.sequence ? `#${current.sequence}` : "Task 상태", current.action].filter(Boolean).join(" · ");
    root.querySelector(".motion-position").textContent = finished ? `재생 완료 · ${steps.length}개 기록` : `현재 재생 · ${index + 1}/${steps.length}`;
    progress.style.width = `${position / duration * 100}%`;
    playButton.textContent = playing ? "일시정지" : "재생";
    playButton.disabled = reduce;
  }

  function tick(now) {
    frameId = 0;
    if (!root.isConnected || !playing || !visible || document.hidden) return;
    if (lastFrame !== null) position += (now - lastFrame) / 1000;
    lastFrame = now;
    if (position >= duration) playing = false;
    seek(position);
    if (playing) frameId = requestAnimationFrame(tick);
  }
  function schedule() {
    if (frameId) cancelAnimationFrame(frameId);
    frameId = 0;
    lastFrame = null;
    if (playing && visible && !document.hidden && !reduce) frameId = requestAnimationFrame(tick);
  }
  function jump(index) {
    playing = false;
    seek(Math.max(0, Math.min(steps.length - 1, index)) * stepSeconds);
    schedule();
  }
  root.querySelector('[data-control="prev"]').addEventListener("click", () => jump(Math.floor(Math.min(position, duration - .001) / stepSeconds) - 1));
  root.querySelector('[data-control="next"]').addEventListener("click", () => jump(Math.floor(Math.min(position, duration - .001) / stepSeconds) + 1));
  playButton.addEventListener("click", () => {
    if (reduce) return;
    if (position >= duration) seek(0);
    playing = !playing;
    seek(position);
    schedule();
  });
  root.querySelector('[data-control="replay"]').addEventListener("click", () => {
    playing = !reduce;
    seek(reduce ? duration : 0);
    schedule();
  });
  document.addEventListener("visibilitychange", schedule);
  if ("ResizeObserver" in window) new ResizeObserver(() => seek(position)).observe(map);
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; schedule(); });
    observer.observe(root);
  }
  seek(position);
  schedule();
})();
</script>
"""
    markup = (
        markup.replace("__ROOT__", root_id).replace("__NODES__", nodes).replace("__DATA__", data)
    )
    st.html(markup, unsafe_allow_javascript=True)
