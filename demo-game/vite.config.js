import { defineConfig } from "vite";
import process from "node:process";

export default defineConfig({
  base: "./",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/game-api": {
        target: process.env.BACKEND_BASE_URL || "http://127.0.0.1:8000",
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/game-api/, "/api/game"),
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
