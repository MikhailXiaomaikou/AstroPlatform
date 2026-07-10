/**
 * Playwright config — minimal headless smoke harness for milestone-level
 * Chat UI verification. CI runs this against an isolated Vite server; tests
 * that need backend state intercept the API explicitly, so they never depend
 * on a live Render deployment or external credentials.
 *
 * Run locally:
 *   npm ci
 *   npx playwright install chromium
 *   npm run test:e2e
 * Or reuse an installed Google Chrome:
 *   PLAYWRIGHT_USE_SYSTEM_CHROME=1 npm run test:e2e
 *
 * Targets either an isolated local Vite server or the deployed
 * Render URL via PLAYWRIGHT_BASE_URL env override.
 */
import { defineConfig, devices } from "@playwright/test";

const chromiumDevice = process.env.PLAYWRIGHT_USE_SYSTEM_CHROME
  ? { ...devices["Desktop Chrome"], channel: "chrome" as const }
  : { ...devices["Desktop Chrome"] };

export default defineConfig({
  testDir: "./playwright",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: chromiumDevice },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
