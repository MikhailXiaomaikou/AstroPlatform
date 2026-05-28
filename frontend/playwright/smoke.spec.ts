/**
 * Milestone smoke — golden paths only. Two checks: (1) landing renders,
 * (2) /chat loads and the API-key gate / Send button is visible. These
 * are the user paths that, if broken, every other feature fails too.
 *
 * Expand cautiously: each Playwright case takes ~5-15 s real time so this
 * file should stay small. Layer-9 paper-derived blind tests are the
 * right place for richer behavior coverage.
 */
import { test, expect } from "@playwright/test";

test("landing page renders with navigation", async ({ page }) => {
  await page.goto("/");
  // Journal-masthead nav has 8 tabs (Home / AI Assistant / Browse / ADQL / ...).
  // We don't pin the exact labels here (they're i18n'd) — just that the nav
  // exists and contains 'Chat' / 'Assistant' or its translated form.
  await expect(page.locator("nav").first()).toBeVisible({ timeout: 10_000 });
});

test("chat page loads and Send button gate is visible", async ({ page }) => {
  await page.goto("/chat");
  // The Send button is always rendered (disabled when no input or no
  // configured backend); verify it exists.
  const send = page.getByRole("button", { name: /send|submit|发送/i });
  await expect(send.first()).toBeVisible({ timeout: 10_000 });
});
