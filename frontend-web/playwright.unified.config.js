import { defineConfig } from "@playwright/test";

// Read-only acceptance against the already running shared local frontend.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "unified-front.spec.js",
  outputDir: "./test-results/unified-front",
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.EVENTQUEST_E2E_BASE_URL ?? "http://127.0.0.1:5177",
    browserName: "chromium",
    channel: "msedge",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
