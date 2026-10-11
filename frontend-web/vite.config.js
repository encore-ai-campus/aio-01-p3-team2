import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      // Test traffic must never fall back to the ordinary API.
      "^/api/test(?:/|$)": process.env.EVENTQUEST_FAULT_API_TARGET || "http://127.0.0.1:8013",
      "/api": process.env.EVENTQUEST_API_TARGET || "http://127.0.0.1:8000",
    },
  },
});
