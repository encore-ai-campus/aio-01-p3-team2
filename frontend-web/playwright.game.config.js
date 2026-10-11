import { defineConfig } from "@playwright/test";

const port = process.env.EVENTQUEST_GAME_E2E_PORT ?? "5193";
export default defineConfig({
  testDir: "./game-e2e",
  outputDir: "./test-results/game",
  workers: 1,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: "chromium",
    channel: process.platform === "win32" ? "msedge" : undefined,
    headless: true,
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `node node_modules/vite/bin/vite.js ../demo-game --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    env: { BACKEND_BASE_URL: process.env.EVENTQUEST_API_TARGET ?? "http://127.0.0.1:8000" },
  },
});
