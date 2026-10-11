import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import path from "node:path";

test("durable asynchronous workflow connects approval and game registration", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "기획안 만들기", exact: true }).click();
  await expect(page.locator("[data-space-row]").first()).toBeVisible();
  for (const row of await page.locator("[data-space-row]").all()) {
    await row.locator('select[name="reward_item"]').selectOption("GOLD");
    await row.locator('input[name="reward_quantity"]').fill("10");
  }
  await page.locator('textarea[name="objective"]').fill("실제 DB 연결과 이벤트 공간 등록 확인");
  const admission = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/event-briefs") && response.request().method() === "POST");
  await page.getByRole("button", { name: /기획안 검증 시작/ }).click();
  const response = await admission;
  expect(response.status()).toBe(202);
  const accepted = await response.json();
  await expect(page.locator(".workflow-board")).toBeVisible();
  await expect(page.locator("[data-approve-plan]")).toBeVisible({ timeout: 15000 });
  const workflow = await page.request.get(`/api/v1/workflows/${accepted.workflow_id}`).then((r) => r.json());
  expect(workflow.current_status).toBe("WAITING_APPROVAL");
  expect(workflow.trace.some((row) => row.event === "supervisor.state_transition_decided")).toBe(true);
  expect(workflow.trace.filter((row) => row.event === "handoff.requested")).toHaveLength(2);
  expect(workflow.recovery_policy_ref.policy_hash).toMatch(/^[a-f0-9]{64}$/);
  await page.locator("[data-approve-plan]").click();
  await expect(page.locator("[data-apply-plan]")).toBeVisible();
  await page.locator("[data-apply-plan]").click();
  await expect(page.getByText("Runtime 이벤트 등록 완료", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator(".page-intro .state-chip")).toHaveText("가상 게임 등록 완료");
  const restored = await page.request.get(`/api/v1/workflows/${accepted.workflow_id}`).then((r) => r.json());
  expect(restored.supervisor_state.status).toBe("APPLIED");
  expect(restored.reconciliation_required).toBe(false);
  expect(restored.actual_effects.runtime_registered).toBe(true);
  const progress = await page.request.get(`/api/game/events/${restored.runtime_event.runtime_event_id}/progress`).then((r) => r.json());
  expect(progress.missions.length).toBe(workflow.plan.missions.length);
  await writeFile(path.join(process.cwd(), "test-results/e2e-restore-state.json"), JSON.stringify({
    workflowId: accepted.workflow_id, runtimeEventId: restored.runtime_event.runtime_event_id,
    contractKind: "space_workflow", stateVersion: restored.supervisor_state.state_version,
    missionCount: progress.missions.length,
  }));
  expect(errors).toEqual([]);
  await page.screenshot({ path: "../docs/ik/ux-evidence/workflow-connected-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("restores durable workflow after backend restart", async ({ page }) => {
  const state = process.env.EVENTQUEST_E2E_RESTORE_STATE
    ? JSON.parse(process.env.EVENTQUEST_E2E_RESTORE_STATE) : null;
  test.skip(!state || state.contractKind !== "space_workflow", "requires the native restart profile");
  await page.addInitScript((id) => localStorage.setItem("eventquest.latestWorkflow", id), state.workflowId);
  await page.goto("/");
  await expect(page.locator(".page-intro .state-chip")).toHaveText("가상 게임 등록 완료");
  const restored = await page.request.get(`/api/v1/workflows/${state.workflowId}`).then((r) => r.json());
  expect(restored.supervisor_state.state_version).toBe(state.stateVersion);
  expect(restored.runtime_event.runtime_event_id).toBe(state.runtimeEventId);
  expect(restored.reconciliation_required).toBe(false);
  const progress = await page.request.get(`/api/game/events/${state.runtimeEventId}/progress`).then((r) => r.json());
  expect(progress.missions).toHaveLength(state.missionCount);
});
