import { defineConfig } from "@playwright/test";

// End-to-end tests run against the real system: start it with `make dev` first.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.AEGIS_SITE_URL ?? "http://127.0.0.1:5173",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
});
