/**
 * Playwright config — minimal headless smoke harness for milestone-level
 * Chat UI verification. Intentionally NOT part of CI gate (per the test
 * plan in /Users/chenkexuan/.claude/plans/scalable-waddling-shannon.md):
 * Playwright pulls a hundred-MB browser bundle on first install, and the
 * golden-path tests need either a live backend (Render) or a vite dev
 * server, both of which are too brittle for every-commit CI.
 *
 * Run locally:
 *   npm install --save-dev @playwright/test
 *   npx playwright install chromium
 *   npx playwright test
 *
 * Targets either a local dev server (VITE preview) or the deployed
 * Render URL via PLAYWRIGHT_BASE_URL env override.
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./playwright",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
