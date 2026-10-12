import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "fault-path-sync.spec.js",
  outputDir: "./test-results/fault-path-sync",
  workers: 1,
  retries: 0,
  use: {baseURL:"http://127.0.0.1:5183",channel:"msedge",headless:true, screenshot:"only-on-failure"},
  webServer: {
    command:"node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5183 --strictPort",
    url:"http://127.0.0.1:5183",
    reuseExistingServer:false,
  },
});
