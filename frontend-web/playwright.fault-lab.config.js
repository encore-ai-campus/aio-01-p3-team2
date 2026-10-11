import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "fault-lab.spec.js",
  outputDir: "./test-results/fault-lab",
  workers: 1,
  retries: 0,
  timeout: 60000,
  reporter: "list",
  use: { baseURL: process.env.EVENTQUEST_E2E_BASE_URL ?? "http://127.0.0.1:5177", browserName: "chromium", channel: "msedge", headless: true,
    viewport: {width:1440,height:1000}, screenshot:"only-on-failure", trace:"retain-on-failure" },
});
