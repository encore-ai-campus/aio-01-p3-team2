import { defineConfig } from "@playwright/test";
import { readFileSync } from "node:fs";

const settings = JSON.parse(readFileSync(process.env.CLOUD_BROWSER_CONFIG, "utf8"));
export default defineConfig({
  testDir: "./e2e", testMatch: "production-images.spec.js", workers: 1, retries: 0,
  forbidOnly: true, timeout: 60000, outputDir: "./test-results/production",
  reporter: [["list"], ["junit", { outputFile: "../test-results/production-browser.xml" }]],
  use: { browserName: "chromium", headless: true, ignoreHTTPSErrors: true,
    httpCredentials: { username: "operator", password: settings.password },
    viewport: { width: 1440, height: 1000 }, screenshot: "only-on-failure", trace: "retain-on-failure",
  },
});
