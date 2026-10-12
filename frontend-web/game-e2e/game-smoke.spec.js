import { expect, test } from "@playwright/test";

test("game canvas and each noncombat tab display authoritative API data without writes", async ({page, request}) => {
  const response = await request.get("/game-api/state");
  expect(response.ok()).toBe(true);
  const snapshot = await response.json();
  let posts = 0;
  const errors = [];
  page.on("request", r => { if(r.method() === "POST") posts++; });
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator("#game-stage canvas")).toBeVisible();
  await expect(page.locator("#player-name")).toHaveText(snapshot.user.display_name);
  await expect(page.locator("#player-level")).toHaveText(String(snapshot.user.level));
  await expect(page.locator("#gold-count")).toHaveText(snapshot.user.inventory.GOLD.toLocaleString("ko-KR"));
  await expect(page.locator("#version-label")).toContainText(snapshot.versions.game_data_snapshot_id);
  for (const [mode, label] of [["growth","원정대 성장"],["stage","달빛 초원 · 스테이지"],["dungeon","별무리 폐광"],["collection","원정대 보관함"],["shop","왕국 꾸미기 상점"]]) {
    await page.locator(`.world-nav [data-mode="${mode}"]`).click();
    await expect(page.locator("#location-name")).toHaveText(label);
    await expect(page.locator("#action-dock")).toBeVisible();
  }
  await page.locator('.world-nav [data-mode="lobby"]').click();
  await expect(page.locator("#location-name")).toHaveText("달빛 원정대의 거점");
  expect(errors).toEqual([]);
  expect(posts).toBe(0);
});

test("game reports backend failure instead of presenting a connected player", async ({page}) => {
  await page.route("**/game-api/state", route => route.fulfill({status:503, json:{message:"게임 저장소 연결을 확인하세요."}}));
  await page.goto("/");
  await expect(page.locator("#guide-copy")).toContainText("게임 저장소 연결을 확인하세요.");
  await expect(page.locator("#player-level")).toHaveText("–");
});

test("game growth and combat commands persist through the actual UI and reload", async ({page, request}, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#game-stage canvas")).toBeVisible();
  await page.locator('.world-nav [data-mode="growth"]').click();
  const before = await request.get("/game-api/state").then(response => response.json());
  const growth = page.locator("[data-growth]:enabled").first();
  await expect(growth).toBeVisible();
  const actionId = await growth.getAttribute("data-growth");
  const growthResponse = page.waitForResponse(response => response.url().endsWith("/game-api/growth-actions") && response.request().method() === "POST");
  await growth.click();
  expect((await growthResponse).ok()).toBe(true);
  await expect.poll(async () => (await request.get("/game-api/state").then(response => response.json())).user.growth_options[actionId].level).toBe(before.user.growth_options[actionId].level + 1);
  await page.reload();
  const saved = await request.get("/game-api/state").then(response => response.json());
  await expect(page.locator("#gold-count")).toHaveText(saved.user.inventory.GOLD.toLocaleString("ko-KR"));
  for (const [mode, selector, command] of [["stage", '[data-hunt="farm"]', "stage_challenge"], ["dungeon", '[data-dungeon-hunt="farm"]', "dungeon_challenge"]]) {
    await page.locator(`.world-nav [data-mode="${mode}"]`).click();
    const battleResponse = page.waitForResponse(response => response.url().endsWith("/game-api/actions") && response.request().postDataJSON()?.command_type === command);
    await page.locator(selector).click();
    const response = await battleResponse;
    expect(response.ok()).toBe(true);
    const battle = await response.json();
    expect(["succeeded", "failed"]).toContain(battle.attempt.result);
    await page.locator("#toggle-hunt").click();
    await expect(page.locator(selector)).toBeVisible({timeout:45000});
    const persisted = await request.get("/game-api/state").then(response => response.json());
    await expect(page.locator("#gold-count")).toHaveText(persisted.user.inventory.GOLD.toLocaleString("ko-KR"));
    await page.screenshot({path:testInfo.outputPath(`${mode}-after-combat.png`),fullPage:true});
  }
  expect(errors).toEqual([]);
});
