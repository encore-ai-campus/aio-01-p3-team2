import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
const settings = JSON.parse(readFileSync(process.env.CLOUD_BROWSER_CONFIG, "utf8"));

test("production planning form reads actual catalog through authenticated HTTPS", async ({ page }) => {
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(settings.plan);
  await page.locator(".login-submit").click();
  await expect(page.locator("[data-space-row]").first()).toBeVisible();
  await expect(page.locator("[data-space-row]")).toHaveCount(3);
  await page.locator('textarea[name="objective"]').fill("운영 이미지 입력 검증");
  await expect(page.locator("#brief-form")).toBeVisible();
  expect(errors).toEqual([]);
});

test("production policy and isolated recovery UI use separate real APIs", async ({ page }) => {
  await page.goto(settings.plan);
  await page.locator(".login-submit").click();
  const readPolicies = () => page.evaluate(async () => {
    const response = await fetch("/api/v1/recovery-policies");
    if (!response.ok) throw new Error("Policy API failed");
    return response.json();
  });
  const before = await readPolicies();
  await page.getByRole("button", { name: "복구 정책", exact: true }).click();
  await expect(page.locator(".policy-route-rows")).toBeVisible();
  await page.getByRole("button", { name: "복구 시연", exact: true }).click();
  await expect(page.getByLabel("격리 시험 환경")).toContainText("연결됨");
  await page.locator('[data-fault-node="planner"]').click();
  await page.locator("#lab-scenario").selectOption("planner_output_invalid");
  await page.locator("#lab-profile").selectOption("retry_allowed");
  await page.locator("#lab-run").click();
  await expect(page.getByRole("heading", { name: "시험 COMPLETED · PASS" })).toBeVisible({ timeout: 45000 });
  expect(await readPolicies()).toEqual(before);
});

test("production game renders Phaser and authoritative persisted state", async ({ page }) => {
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(settings.game);
  const state = await page.evaluate(async () => {
    const response = await fetch("/game-api/state");
    if (!response.ok) throw new Error("Game API failed");
    return response.json();
  });
  await expect(page.locator("#game-stage canvas")).toBeVisible();
  await expect(page.locator("#player-name")).toHaveText(state.user.display_name);
  for (const mode of ["growth", "stage", "dungeon", "collection", "shop", "lobby"]) {
    await page.locator(`.world-nav [data-mode="${mode}"]`).click();
    await expect(page.locator("#action-dock")).toBeVisible();
  }
  expect(errors).toEqual([]);
});
