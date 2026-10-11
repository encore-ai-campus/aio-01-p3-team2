import { defineConfig } from "@playwright/test";

const apiPort = process.env.EVENTQUEST_E2E_API_PORT ?? "8004";
const uiPort = process.env.EVENTQUEST_E2E_UI_PORT ?? "5177";
const baseURL = process.env.EVENTQUEST_E2E_BASE_URL ?? `http://127.0.0.1:${uiPort}`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: "list",
  use: {
    baseURL,
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `pnpm dev --host 127.0.0.1 --port ${uiPort} --strictPort`,
    cwd: process.cwd(),
    url: baseURL,
    reuseExistingServer: false,
    env: {
      EVENTQUEST_API_TARGET:
        process.env.EVENTQUEST_API_TARGET ?? `http://127.0.0.1:${apiPort}`,
    },
  },
});
