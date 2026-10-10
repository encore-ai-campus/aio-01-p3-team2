import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/api": process.env.EVENTQUEST_API_TARGET || "http://127.0.0.1:8000",
    },
  },
});
