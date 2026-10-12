// The run report pins both its observed event order and its node mapping.
export function executionPathModel(topology, result) {
  const pinned = result?.path_schema_version === 1 && result?.path_topology?.topology_id === topology.topology_id;
  const rows = pinned ? result.execution_path ?? [] : result?.trace ?? [];
  const steps = rows.map((row, index) => {
    const node = pinned
      ? topology.nodes.find(n => n.node_id === row.node_id)
      : topology.nodes.find(n => row.event === "workflow.safe_stopped" ? n.node_id === "safe_stop" : n.observed_events.includes(row.event) || n.observed_events.includes(row.action));
    return {...row, sequence: row.sequence ?? index + 1, node_id: node?.node_id ?? null};
  });
  // Missing mappings remain visible and break the path; they never create a false shortcut.
  const transitions = steps.flatMap((step, index) => {
    const previous = steps[index - 1];
    return previous?.node_id && step.node_id ? [{from:previous.node_id, to:step.node_id, index}] : [];
  });
  return {steps, transitions, legacy: !pinned};
}

export function executionStepLabel(step) {
  if (["storage.result_persisted", "planning.record_persisted"].includes(step.event)) return "기획 결과·Trace 저장 완료";
  if (step.kind === "injection") return `오류 주입${step.call_index ? ` · 호출 ${step.call_index}회차` : ""}`;
  if (step.kind === "detection") return "실제 오류 검출";
  if (step.event === "workflow.safe_stopped") return "안전 종료";
  if (step.event === "rule_balance.validation_completed" && step.phase === "revalidation") return "수정 후 게임 규칙·보상 재검증";
  if (step.event?.endsWith(".recovery_decided")) return "Supervisor 복구 결정";
  if (step.event?.endsWith(".recovery_resolved")) return "복구 작업 완료";
  return ({"brief.accepted":"기획 요청 확인", "planner.started":"기획 Agent 실행", "planner.output_validated":"기획 결과 검증", "runtime.evidence_collected":"게임 데이터 조회 완료", "rule_balance.validation_completed":"게임 규칙·보상 검증", "critic.review_completed":"기획안 검수", "final_consistency.checked":"최종 일관성 확인", "approval.pending":"운영자 승인 대기", "supervisor.state_transition_decided":"Supervisor 상태 전이", "handoff.requested":"Agent 인계 요청", "handoff.accepted":"Agent 인계 확인", "handoff.rejected":"Agent 인계 거절"})[step.event] ?? step.event ?? step.action ?? "관측 사건";
}
