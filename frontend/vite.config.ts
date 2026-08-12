import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// 开发时前端跑在 5173，/api 代理到 FastAPI 网关（8088），规避跨域、统一同源体验。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8088",
        changeOrigin: true,
      },
    },
  },
});
