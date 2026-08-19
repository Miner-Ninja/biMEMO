import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
const shouldStartWebServer = false;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  // The manual-gated setup-first-run scenario needs a stack whose password
  // is not set yet, so the shared login cannot be pre-established: allow
  // skipping the global setup for that manual run.
  globalSetup: process.env.E2E_SKIP_GLOBAL_SETUP
    ? undefined
    : "./tests/e2e/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    storageState: "./tests/e2e/.auth/state.json",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: shouldStartWebServer
    ? {
        command:
          "npx vite build --outDir .playwright-dist && npx vite preview --host 127.0.0.1 --port 4173 --outDir .playwright-dist",
        url: "http://127.0.0.1:4173",
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      }
    : undefined,
});
