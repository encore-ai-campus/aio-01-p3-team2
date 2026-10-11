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
