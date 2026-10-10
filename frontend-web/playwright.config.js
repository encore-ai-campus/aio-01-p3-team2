import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.EVENTQUEST_E2E_BASE_URL ?? "http://127.0.0.1:5177",
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm dev --host 127.0.0.1 --port 5177 --strictPort",
    cwd: process.cwd(),
    url: "http://127.0.0.1:5177",
    reuseExistingServer: false,
    env: {
      EVENTQUEST_API_TARGET:
        process.env.EVENTQUEST_API_TARGET ?? "http://127.0.0.1:8004",
    },
  },
});
