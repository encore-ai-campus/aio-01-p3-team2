import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir:"./e2e",
  testMatch:"fault-path-real.spec.js",
  outputDir:process.env.EVENTQUEST_REAL_PATH_OUTPUT_DIR ?? "./test-results/fault-path-real",
  workers:1,
  retries:0,
  timeout:180000,
  use:{baseURL:"http://127.0.0.1:5184",channel:"msedge",headless:true,screenshot:"only-on-failure",trace:"retain-on-failure",viewport:{width:1440,height:1000}},
  webServer:{
    command:"node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5184 --strictPort",
    url:"http://127.0.0.1:5184",
    reuseExistingServer:false,
    env:{EVENTQUEST_FAULT_API_TARGET:"http://127.0.0.1:8183",EVENTQUEST_API_TARGET:"http://127.0.0.1:8183"},
  },
});
