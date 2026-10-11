import { expect, test } from "@playwright/test";

test("shared UI reads separate ordinary and fault databases without replay", async ({page, request}) => {
  test.skip(!process.env.UNIFIED_FRONT_CHECK, "Requires the shared UI and both local backends.");
  const normal = await (await request.get("/api/v1/recovery-policies")).json();
  const status = await (await request.get("/api/test/fault-status")).json();
  expect(status.environment).toBe("isolated");
  expect(status.database).toBe("eventquest_fault_test");
  expect(status.state).toBe("CLEAN");
  expect(normal.versions.some(v => v.status === "ACTIVE")).toBe(true);
  let posts = 0;
  page.on("request", r => {if(r.method() === "POST") posts++;});
  await page.goto("/");
  await page.getByRole("button", {name:"복구 시연", exact:true}).click();
  await expect(page.getByLabel("격리 시험 환경")).toContainText(`시험 정책 버전: ${status.active_policy_version}`);
  await page.locator("#lab-record-id").fill("e6b8ec4b-439f-4114-942d-0542def3a7d6");
  await page.getByRole("button", {name:"기록 ID로 조회", exact:true}).click();
  await expect(page.getByLabel("시험 결과 설명")).toContainText("오류 대응 후 승인 대기까지 진행");
  await page.reload();
  await page.getByRole("button", {name:"복구 시연", exact:true}).click();
  await expect(page.locator("#lab-report")).toContainText("e6b8ec4b-439f-4114-942d-0542def3a7d6");
  expect(posts).toBe(0);
});

test("fault connection failure blocks only the test screen and can reconnect", async ({page}) => {
  test.skip(!process.env.UNIFIED_FRONT_CHECK, "Requires the shared UI and both local backends.");
  await page.goto("/");
  await page.route("**/api/test/fault-status", route => route.fulfill({status:503, body:"offline"}));
  await page.getByRole("button", {name:"복구 시연", exact:true}).click();
  await expect(page.getByRole("heading", {name:"격리 시험실 연결 필요"})).toBeVisible();
  await expect(page.locator("#lab-run")).toHaveCount(0);
  await page.unroute("**/api/test/fault-status");
  await page.getByRole("button", {name:"시험 연결 다시 확인"}).click();
  await expect(page.getByLabel("격리 시험 환경")).toContainText("연결됨");
  await page.getByRole("button", {name:"기획안 만들기", exact:true}).click();
  await expect(page.locator("#fault-lab")).toHaveCount(0);
});

test("shared UI executes isolated recovery while ordinary policy remains unchanged", async ({page, request}) => {
  test.skip(!process.env.UNIFIED_FRONT_CHECK, "Requires the shared UI and both local backends.");
  const before = await (await request.get("/api/v1/recovery-policies")).json();
  await page.goto("/");
  await page.getByRole("button", {name:"복구 시연", exact:true}).click();
  await page.locator('[data-fault-node="planner"]').click();
  await page.locator("#lab-scenario").selectOption("planner_output_invalid");
  await page.locator("#lab-profile").selectOption("retry_allowed");
  await page.locator("#lab-run").click();
  await expect(page.getByRole("heading", {name:"시험 COMPLETED · PASS"})).toBeVisible({timeout:45000});
  await expect(page.getByLabel("시험 결과 설명")).toContainText("자동 복구 후 작업을 계속");
  expect(await (await request.get("/api/v1/recovery-policies")).json()).toEqual(before);
  expect((await (await request.get("/api/test/fault-status")).json()).state).toBe("CLEAN");
  await page.getByRole("button", {name:"기획안 만들기", exact:true}).click();
  await expect(page.locator("#fault-lab")).toHaveCount(0);
});
