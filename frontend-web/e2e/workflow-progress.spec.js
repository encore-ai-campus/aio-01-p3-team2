import { expect, test } from "@playwright/test";
import { buildWorkflowGraph } from "../src/workflow-progress.js";

const step = (event, status = "SUCCESS", extra = {}) => ({ event, status, ...extra });
function fixture(kind) {
  const trace = [
    step("brief.accepted"), step("runtime.evidence_collected"),
    step("handoff.accepted", "SUCCESS", { from_agent: "supervisor", to_agent: "canonical_event_planner_agent" }),
    step("planner.output_validated"), step("capability.scope_checked", "SUPPORTED"),
    step("rule_balance.validation_completed", "PASS"),
    step("handoff.accepted", "SUCCESS", { from_agent: "supervisor", to_agent: "canonical_event_critic_agent" }),
    step("critic.review_completed", "PASS"), step("final_consistency.checked", "VALIDATED"),
    step("approval.pending", "WAITING_APPROVAL"),
  ];
  const run = {
    workflow_id: "WF-GRAPH-" + kind, task_id: "TASK-GRAPH", execution_profile: "fixture",
    workflow_status: "WAITING_APPROVAL", current_status: "WAITING_APPROVAL",
    plan: {
      plan_id: "PLAN-GRAPH", result_version: 1, plan_status: "VALIDATED", runtime_eligible: true,
      game_context: { game_data_snapshot_id: "SNAPSHOT-GRAPH" },
      missions: [], rewards: [], development_requirements: [], impact_assessment: [], evidence_refs: [],
      validation_summary: { status: "PASS", findings: [] },
    },
    agent_analysis: { critic: { status: "PASS", review_note: "저장된 규칙과 기획안을 검토했습니다." } },
    trace,
  };
  if (kind === "review" || kind === "blocked") {
    trace.pop();
    run.plan.runtime_eligible = false;
    run.plan.plan_status = "BLOCKED";
    run.plan.validation_summary.findings = [{ code: "ACTION_NOT_SUPPORTED", message_safe: "지원되지 않는 행동입니다.", suggested_action: "지원 범위를 확인하세요." }];
    trace.at(-1).status = "BLOCKED";
    run.workflow_status = run.current_status = kind === "review" ? "WAITING_REVIEW" : "COMPLETED";
    if (kind === "review") {
      run.agent_analysis.critic.status = "REVIEW";
      trace.find((item) => item.event === "critic.review_completed").status = "REVIEW";
    }
  }
  if (kind === "recovery") {
    trace.splice(1, 0,
      step("mcp.recovery_decided", "RETRY", { agent: "context_agent", reason: "조회가 지연되어 재시도합니다." }),
      step("mcp.recovery_resolved", "SUCCESS", { agent: "context_agent" }),
    );
  }
  if (kind === "safe-stop") {
    delete run.plan;
    delete run.agent_analysis;
    run.workflow_status = run.current_status = "SAFE_STOP";
    run.error = { code: "ERR-DEP-MCP-001", message: "연결 오류로 안전하게 중단했습니다." };
    run.trace = [step("brief.accepted"), step("mcp.recovery_decided", "RETRY"), step("mcp.recovery_decided", "SAFE_STOP"), step("workflow.safe_stopped", "SAFE_STOP")];
  }
  if (["approved", "rejected", "applied", "missing-runtime-trace"].includes(kind)) {
    const status = kind === "rejected" ? "REJECTED" : "APPROVED";
    run.decision = { approval_status: status };
    run.current_status = status;
    trace.push(step("approval.decision_recorded", status));
    if (["applied", "missing-runtime-trace"].includes(kind)) {
      run.runtime_event = { status: "applied" };
      run.current_status = "APPLIED";
      if (kind === "applied") trace.push(step("runtime.event_registered", "APPLIED"));
    }
  }
  if (kind === "legacy") {
    delete run.agent_analysis;
    run.trace = [];
  }
  run.trace.forEach((item, index) => { item.sequence = index + 1; });
  return run;
}

for (const kind of ["success", "review", "blocked", "recovery", "safe-stop", "approved", "rejected", "applied", "missing-runtime-trace", "legacy"]) {
  test("connected Workflow graph supports " + kind, async ({ page }) => {
    const run = fixture(kind);
    const graph = buildWorkflowGraph(run);
    expect(graph.edges).toHaveLength(Math.max(0, graph.nodes.length - 1));
    expect(new Set(graph.nodes.map((node) => node.id)).size).toBe(graph.nodes.length);
    for (const node of graph.nodes.filter((node) => node.lane === "main")) {
      expect(node.event).not.toMatch(/handoff|recovery/);
    }
    if (kind === "success") {
      expect(graph.edges.some((edge) => edge.kind === "branch")).toBe(true);
      expect(graph.edges.some((edge) => edge.kind === "merge")).toBe(true);
    }
    if (["review", "blocked", "safe-stop"].includes(kind)) {
      expect(graph.nodes.some((node) => /approval\.|runtime\.event_registered/.test(node.event))).toBe(false);
    }
    if (kind === "review") expect(graph.nodes.at(-1).event).toBe("workflow.human_review");
    if (kind === "safe-stop") expect(graph.edges.at(-1).kind).toBe("stop");
    if (kind === "rejected") expect(graph.nodes.at(-1).status).toBe("REJECTED");
    if (kind === "missing-runtime-trace") expect(graph.nodes.at(-1).recordType).toBe("runtime");
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", (route) => {
      const url = route.request().url();
      const body = url.includes("/workflows") ? [run]
        : url.includes("special_dungeon_templates") ? { templates: [{ name: "이벤트", spaces: [] }], versions: { game_data_snapshot_id: "SNAPSHOT" } }
        : { items: [], versions: { game_data_snapshot_id: "SNAPSHOT" } };
      return route.fulfill({ json: body });
    });
    await page.goto("/");
  await page.locator(".login-submit").click();
    await expect(page.locator(".workflow-board")).toHaveCount(1);
    await expect(page.locator(".route-node")).toHaveCount(graph.nodes.length);
    await expect(page.locator(".route-edge")).toHaveCount(graph.edges.length);
    await expect(page.locator('.route-node[data-lane="main"][data-event="handoff.accepted"]')).toHaveCount(0);
    const clipping = await page.locator(".route-node").evaluateAll((nodes) => nodes.filter((node) => node.scrollHeight > node.clientHeight + 1).length);
    expect(clipping).toBe(0);
    expect(errors).toEqual([]);
    if (["success", "recovery", "review", "safe-stop"].includes(kind)) {
      await page.locator(".workflow-board").screenshot({ path: test.info().outputPath("workflow-graph-" + kind + "-desktop.png") });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    const scroll = page.locator(".workflow-route-scroll");
    await scroll.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    expect(await scroll.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    if (kind === "success") {
      await scroll.evaluate((element) => { element.scrollLeft = 0; });
      await page.screenshot({ path: test.info().outputPath("workflow-graph-success-mobile.png"), fullPage: true });
    }
  });
}

test("unknown and incomplete records stay inspectable without invented retries", () => {
  const run = { trace: [step("brief.accepted"), step("future.check_completed", "NEW_STATE")], workflow_status: "COMPLETED" };
  const graph = buildWorkflowGraph(run);
  expect(graph.nodes.map((node) => node.event)).toEqual(run.trace.map((item) => item.event));
  expect(graph.nodes.some((node) => node.lane === "supervisor")).toBe(false);
});

test("Planner start and recorded failure remain visible on the main flow", async ({ page }) => {
  const run = {
    workflow_id: "WF-PLANNER-FAILURE", current_status: "SAFE_STOP", workflow_status: "SAFE_STOP",
    error: { code: "ERR-CONTRACT-PLANNER-001", message: "기획 결과 검증 실패" },
    trace: [step("brief.accepted"), step("planner.started", "STARTED"), step("agent.recovery_decided", "SAFE_STOP"), step("workflow.safe_stopped", "SAFE_STOP")],
    llm_usage: { records: [{ prompt_template_id: "canonical_event_planner_agent", record_id: "record-1" }] },
  };
  expect(buildWorkflowGraph(run).nodes.filter((node) => node.event === "planner.started")).toHaveLength(1);
  expect(buildWorkflowGraph(run).nodes.some((node) => node.event === "planner.execution_recorded")).toBe(false);
  const legacy = { ...run, trace: run.trace.filter((row) => row.event !== "planner.started") };
  expect(buildWorkflowGraph(legacy).nodes.find((node) => node.event === "planner.execution_recorded").lane).toBe("main");
  expect(buildWorkflowGraph({ ...legacy, llm_usage: undefined }).nodes.some((node) => node.event === "planner.execution_recorded")).toBe(false);
  await page.route("**/api/**", (route) => route.fulfill({ json: route.request().url().includes("/workflows") ? [run] : { items: [], templates: [], versions: {} } }));
  await page.goto("/");
  await page.locator(".login-submit").click();
  await expect(page.locator('.route-node[data-lane="main"][data-event="planner.started"]')).toContainText("Planner 기획 시작");
  await expect(page.locator('.route-node[data-event="planner.output_validated"]')).toHaveCount(0);
});

test("live progress resumes polling after reload and renders Supervisor event aliases", async ({ page }) => {
  const completed = fixture("success");
  const running = {
    workflow_id: completed.workflow_id, current_status: "PLANNING", workflow_status: "PLANNING",
    supervisor_state: { status: "PLANNING", state_version: 2 },
    operation_status: "RUNNING", trace: [
      step("supervisor.transition_decided", "PLANNING", { action: "RUN_PLANNER" }),
      step("supervisor.state_transition_decided", "PLANNING", { action: "RETRY_AGENT" }),
    ],
  };
  let finish = false;
  await page.route("**/api/**", (route) => {
    const url = route.request().url();
    const current = finish ? completed : running;
    const body = url.includes("/workflows?") ? [current]
      : url.includes("/workflows/") ? current
      : url.includes("special_dungeon_templates") ? { templates: [{ name: "이벤트", spaces: [] }], versions: { game_data_snapshot_id: "SNAPSHOT" } }
      : { items: [], versions: { game_data_snapshot_id: "SNAPSHOT" } };
    return route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.locator(".login-submit").click();
  await expect(page.getByText("기획안을 처리하고 있습니다", { exact: true })).toBeVisible();
  await expect(page.locator('.route-node[data-lane="supervisor"]')).toHaveCount(2);
  await page.reload();
  await page.locator(".login-submit").click();
  await expect(page.getByText("기획안을 처리하고 있습니다", { exact: true })).toBeVisible();
  finish = true;
  await expect(page.locator("[data-approve-plan]")).toBeVisible();
  await expect(page.getByText("기획안을 처리하고 있습니다", { exact: true })).toHaveCount(0);
});
