import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

const gateway = process.env.PRAHARI_GATEWAY_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      "/api": gateway,
      "/v1": { target: gateway, ws: true }, // /v1/ws/chat is a WebSocket
      "/ws": { target: gateway.replace("http", "ws"), ws: true },
    },
  },
  build: {
    target: "es2022",
    // No manual chunks: three.js, ECharts, React Flow and the markdown renderer are each behind a
    // lazy import already, so the automatic split keeps them off the first page. (Manual chunk rules
    // swept shared helpers into the three.js and React Flow chunks, which Home then had to preload.)
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
