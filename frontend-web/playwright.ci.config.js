import { defineConfig } from "@playwright/test";

const phase = process.env.CI_UI_PHASE ?? "connected";
const phases = {
  connected: { testMatch: ["planning-flow.spec.js", "workflow-connected.spec.js", "workflow-progress.spec.js"], grepInvert: /real MCP HTTP 503|restores durable workflow/ },
  restart: { testMatch: "workflow-connected.spec.js", grep: /restores durable workflow/ },
  mcp503: { testMatch: "planning-flow.spec.js", grep: /real MCP HTTP 503/ },
  game: { testDir: "./game-e2e", testMatch: "game-smoke.spec.js" },
};
if (!phases[phase]) throw new Error("Unknown CI UI phase");
const game = phase === "game";
const port = game ? "5193" : "5197";
export default defineConfig({
  testDir: "./e2e", ...phases[phase],
  outputDir: `./test-results/ci-${phase}`, workers: 1, retries: 0,
  forbidOnly: true, timeout: 60000,
  reporter: [["list"], ["junit", { outputFile: `../test-results/ui-${phase}.xml` }]],
  use: { baseURL: `http://127.0.0.1:${port}`, browserName: "chromium", headless: true,
    viewport: { width: 1440, height: 1000 }, screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: {
    command: `node node_modules/vite/bin/vite.js ${game ? "../demo-game" : ""} --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`, reuseExistingServer: false,
    env: { EVENTQUEST_API_TARGET: "http://127.0.0.1:8015", BACKEND_BASE_URL: "http://127.0.0.1:8015" },
  },
});
