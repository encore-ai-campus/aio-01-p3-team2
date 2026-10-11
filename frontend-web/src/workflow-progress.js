const MAIN_EVENTS = new Set([
  "brief.accepted", "planner.started", "planner.execution_recorded", "planner.output_validated", "approval.pending",
  "approval.decision_recorded", "runtime.event_registered", "workflow.safe_stopped",
]);
const OMITTED_EVENTS = new Set(["llm.usage_recorded"]);
const LANES = { main: 24, review: 348, supervisor: 672 };
const NODE_WIDTH = 260;
const NODE_HEIGHT = 86;
const ROW_HEIGHT = 128;

function laneFor(step) {
  if (step.event?.startsWith("handoff.") || ["supervisor.transition_decided", "supervisor.state_transition_decided"].includes(step.event) || /recovery_(decided|resolved)/.test(step.event ?? "")) return "supervisor";
  return MAIN_EVENTS.has(step.event) ? "main" : "review";
}

function handoffTarget(step, next) {
  if (step?.event !== "handoff.accepted" || !next) return false;
  const target = step.to_agent ?? step.attributes?.to_agent ?? "";
  return (/planner/.test(target) && next.event === "planner.output_validated")
    || (/critic/.test(target) && next.event === "critic.review_completed");
}

// The graph displays the recorded process order. It does not infer finding-to-retry causality.
export function buildWorkflowGraph(run) {
  const trace = Array.isArray(run.trace) ? run.trace : [];
  const nodes = trace.flatMap((step, index) => OMITTED_EVENTS.has(step.event) ? [] : [{
    id: "trace-" + index,
    event: step.event ?? "unknown",
    status: step.status ?? "UNKNOWN",
    lane: laneFor(step),
    sequence: step.sequence ?? index + 1,
    recordType: "trace",
    step,
  }]);
  // Older failed runs have no start observation. Only a stored provider record
  // proves Planner actually ran; a handoff request alone does not prove execution.
  const plannerRecord = run.llm_usage?.records?.find((record) => record.prompt_template_id === "canonical_event_planner_agent");
  if (plannerRecord && !nodes.some((node) => ["planner.started", "planner.output_validated"].includes(node.event))) {
    const stoppedAtPlanner = run.error?.code === "ERR-CONTRACT-PLANNER-001";
    const node = { id: "planner-execution-record", event: "planner.execution_recorded", lane: "main", recordType: "llm", status: stoppedAtPlanner ? "ERROR" : "SUCCESS", step: { reason: stoppedAtPlanner ? "호출 완료 후 출력 계약 검증 실패" : "저장된 LLM 호출 기록", record_id: plannerRecord.record_id } };
    const endIndex = nodes.findIndex((item) => item.step?.error_code || item.event === "workflow.safe_stopped");
    nodes.splice(endIndex < 0 ? nodes.length : endIndex, 0, node);
  }
  if (run.plan) {
    const outcome = {
      id: "plan-outcome", event: "plan.result", lane: "main", recordType: "plan",
      status: run.plan.plan_status ?? "UNKNOWN", step: {},
    };
    const lifecycleIndex = nodes.findIndex((node) => /^(approval\.|runtime\.event_registered|workflow\.safe_stopped)/.test(node.event));
    nodes.splice(lifecycleIndex < 0 ? nodes.length : lifecycleIndex, 0, outcome);
  }
  const decision = run.decision ?? run.plan?.decision;
  if (decision && !nodes.some((node) => node.event === "approval.decision_recorded")) {
    const node = { id: "decision-result", event: "approval.decision_recorded", lane: "main", recordType: "decision", status: decision.approval_status, step: decision };
    const runtimeIndex = nodes.findIndex((item) => item.event === "runtime.event_registered");
    nodes.splice(runtimeIndex < 0 ? nodes.length : runtimeIndex, 0, node);
  }
  const runtime = run.runtimeEvent ?? run.runtime_event;
  if (runtime && !nodes.some((node) => node.event === "runtime.event_registered")) {
    nodes.push({ id: "runtime-result", event: "runtime.event_registered", lane: "main", recordType: "runtime", status: "APPLIED", step: runtime });
  }
  const stopped = ["SAFE_STOP", "FAILED"].includes(run.current_status ?? run.workflow_status);
  if (stopped && !nodes.some((node) => node.event === "workflow.safe_stopped")) {
    nodes.push({ id: "stopped-result", event: "workflow.safe_stopped", lane: "main", recordType: "result", status: "SAFE_STOP", step: run.error ?? {} });
  } else if (!stopped && ((run.current_status ?? run.workflow_status) === "WAITING_REVIEW" || (!run.current_status && !run.workflow_status && run.agent_analysis?.critic?.status === "REVIEW"))) {
    nodes.push({ id: "human-review", event: "workflow.human_review", lane: "main", recordType: "result", status: "WAITING_REVIEW", step: {} });
  }
  let row = -1;
  nodes.forEach((node, index) => {
    if (!handoffTarget(nodes[index - 1]?.step, node)) row += 1;
    node.x = LANES[node.lane];
    node.y = 56 + row * ROW_HEIGHT;
    node.width = NODE_WIDTH;
    node.height = NODE_HEIGHT;
  });
  const edges = nodes.slice(1).map((to, index) => {
    const from = nodes[index];
    const kind = to.event === "workflow.safe_stopped" ? "stop"
      : from.lane === "supervisor" || to.lane === "supervisor" ? "control"
      : from.lane === "main" && to.lane === "review" ? "branch"
      : from.lane === "review" && to.lane === "main" ? "merge" : "next";
    return { id: "edge-" + index, from: from.id, to: to.id, kind };
  });
  return { nodes, edges, width: 956, height: Math.max(210, 56 + (row + 1) * ROW_HEIGHT), trace };
}

function edgePath(from, to) {
  if (from.y === to.y) {
    return "M " + from.x + " " + (from.y + NODE_HEIGHT / 2) + " L " + (to.x + NODE_WIDTH) + " " + (to.y + NODE_HEIGHT / 2);
  }
  const sx = from.x + NODE_WIDTH / 2;
  const sy = from.y + NODE_HEIGHT;
  const tx = to.x + NODE_WIDTH / 2;
  const ty = to.y;
  const mid = (sy + ty) / 2;
  return "M " + sx + " " + sy + " C " + sx + " " + mid + ", " + tx + " " + mid + ", " + tx + " " + ty;
}

export function renderWorkflowGraph(run, labels) {
  const graph = buildWorkflowGraph(run);
  const { escapeHtml: escape, traceEventLabel, traceStatusLabel, traceStatusTone, planStatusLabel } = labels;
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const main = graph.nodes.filter((node) => node.lane === "main");
  const spine = main.slice(1).map((to, index) => {
    const from = main[index];
    return '<path class="route-spine" d="M ' + (from.x + NODE_WIDTH / 2) + ' ' + (from.y + NODE_HEIGHT) + ' L ' + (to.x + NODE_WIDTH / 2) + ' ' + to.y + '" />';
  }).join("");
  const edges = graph.edges.map((edge) => {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    const color = edge.kind === "stop" ? "stop" : edge.kind === "control" ? "control" : "process";
    return '<path class="route-edge route-edge-' + color + '" data-from="' + edge.from + '" data-to="' + edge.to + '" data-kind="' + edge.kind + '" d="' + edgePath(from, to) + '" marker-end="url(#route-arrow-' + color + ')" />';
  }).join("");
  const cards = graph.nodes.map((node) => {
    const step = { ...node.step, ...node.step.attributes };
    const tone = traceStatusTone(node.status);
    const label = node.event === "plan.result" ? "기획안 결과 · V" + (run.plan.result_version ?? "?")
      : node.event === "workflow.human_review" ? "사람 검토 대기"
      : node.event === "planner.started" ? "Planner 기획 시작"
      : node.event === "planner.execution_recorded" ? "Planner 기획 실행"
      : node.event === "planner.output_validated" ? "Planner 기획안 생성"
      : node.event === "handoff.accepted" ? "Supervisor 담당 단계 전달"
      : traceEventLabel(node.event) === "추가 실행 단계" ? node.event : traceEventLabel(node.event);
    const status = node.event === "plan.result" ? planStatusLabel(run.plan) : traceStatusLabel(node.status);
    const detail = node.event === "critic.review_completed" ? run.agent_analysis?.critic?.review_note ?? run.agent_analysis?.critic?.reason
      : node.event === "plan.result" ? (run.plan.validation_summary?.findings?.length ?? 0) + "개 검증 항목 · " + (run.plan.runtime_eligible ? "Runtime 지원 가능" : "후속 확인 필요")
      : node.event === "handoff.accepted" ? "담당: " + (step.to_agent ?? "기록된 Agent")
      : step.reason ?? step.message ?? step.snapshot_id ?? "";
    const record = node.recordType === "trace" ? "Trace " + node.sequence : node.recordType === "llm" ? "저장된 LLM 호출" : "저장된 결과";
    return '<foreignObject x="' + node.x + '" y="' + node.y + '" width="' + NODE_WIDTH + '" height="' + NODE_HEIGHT + '"><article xmlns="http://www.w3.org/1999/xhtml" class="route-node ' + node.lane + ' ' + tone + '" data-node-id="' + node.id + '" data-lane="' + node.lane + '" data-event="' + escape(node.event) + '" title="' + escape(detail) + '"><div class="route-node-top"><small>' + escape(record) + '</small><span>' + escape(status === "확인 필요" ? node.status : status) + '</span></div><b>' + escape(label) + '</b><p>' + escape(detail || (node.lane === "supervisor" ? "실행 경로 조정" : "단계 결과: " + status)) + '</p></article></foreignObject>';
  }).join("");
  const markers = ["process", "control", "stop"].map((name) => '<marker id="route-arrow-' + name + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="route-arrow-' + name + '" d="M 0 0 L 10 5 L 0 10 z" /></marker>').join("");
  const headings = Object.entries(LANES).map(([lane, x]) => '<text class="route-lane-title" x="' + x + '" y="25">' + ({ main: "주 흐름", review: "검증 · 평가", supervisor: "Supervisor · 복구" })[lane] + '</text>').join("");
  return '<div class="workflow-route-scroll" tabindex="0" aria-label="Workflow 경로, 좁은 화면에서 좌우로 이동"><svg class="workflow-route-map" viewBox="0 0 ' + graph.width + ' ' + graph.height + '" role="group" aria-label="주 흐름과 연결된 검증·평가·복구 경로"><defs>' + markers + '</defs>' + headings + spine + edges + cards + (graph.nodes.length ? "" : '<text x="24" y="95" class="route-lane-title">저장된 실행 단계가 없습니다.</text>') + '</svg></div><p class="route-pan-hint">옆 흐름은 좌우로 스크롤해 확인하세요.</p>';
}
