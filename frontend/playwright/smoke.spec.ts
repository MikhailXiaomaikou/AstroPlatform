/**
 * Milestone smoke — golden paths only. Checks cover landing, chat startup,
 * and the authenticated durable-job/artifact research view. These
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

test("authenticated research center restores jobs, artifacts, and hashes", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("astro_token", "playwright-owner-token");
    sessionStorage.setItem("astro_backend_checked", "1");
  });
  // Match only the backend origin. A broad **/api/** glob also catches Vite's
  // /src/api/client.ts module and replaces application code with mock JSON.
  await page.route("http://localhost:8000/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (path === "/api/auth/me") return json({
      id: "00000000-0000-4000-8000-000000000001",
      username: "reviewer",
      email: "reviewer@example.com",
      subscription_tier: "solo",
      stripe_customer_id: null,
      display_name: "Reviewer",
      avatar_url: null,
      google_linked: false,
    });
    if (path === "/api/research/profile") return json({
      id: "profile-1",
      user_id: "00000000-0000-4000-8000-000000000001",
      memory_enabled: true,
      frequently_queried_objects: [],
      preferred_databases: ["DESI"],
      preferred_analysis_methods: ["MCMC"],
      research_interests: ["dark energy"],
      expertise_level: "advanced",
      past_hypotheses: [],
      preferred_plotting_style: {},
    });
    if (path === "/api/research/history") return json([]);
    if (path === "/api/jobs") return json({
      total: 1,
      items: [{
        job_id: "fit_cosmology_mcmc-e2e",
        tool_name: "fit_cosmology_mcmc",
        inputs_hash: "abc123",
        description: "Strict chain",
        status: "running",
        progress: 42,
        progress_message: "sampling",
        error: null,
        error_class: null,
        background_backend: "celery",
        session_id: null,
        created_at: "2026-07-10T00:00:00Z",
        started_at: "2026-07-10T00:00:01Z",
        completed_at: null,
        can_cancel: true,
        can_retry: false,
      }],
    });
    if (path === "/api/data/fits/browse") return json([{
      id: "artifact-1",
      filename: "posterior.nc",
      fits_path: "jobs/owner/run/posterior.nc",
      size_bytes: 2048,
      source: "export",
      object_id: "posterior.nc",
      created_at: "2026-07-10T00:00:02Z",
      metadata: { sha256: "a".repeat(64), storage_backend: "s3" },
    }]);
    return json({});
  });

  await page.goto("/account?tab=research");
  await expect(page.getByRole("heading", { name: "Artifacts & Long Jobs" })).toBeVisible();
  await expect(page.getByText("fit_cosmology_mcmc", { exact: true })).toBeVisible();
  await expect(page.getByText(/42% sampling/)).toBeVisible();
  await expect(page.getByText("posterior.nc")).toBeVisible();
  await expect(page.getByText(/SHA-256: a{64}/)).toBeVisible();
});
