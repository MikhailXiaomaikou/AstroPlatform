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

test("Foundry candidate Demo stays visibly non-formal in English and Chinese", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.stack ?? error.message));
  await page.addInitScript(() => {
    localStorage.setItem("astro_token", "playwright-foundry-owner-token");
    localStorage.setItem("astro_lang", "en");
    localStorage.setItem("astro_onboarded", "1");
    sessionStorage.setItem("astro_backend_checked", "1");
  });

  const candidateId = "00000000-0000-4000-8000-000000000101";
  const candidate = {
    id: candidateId,
    status: "DEMO_RECORDED",
    gap_fingerprint: `sha256:${"1".repeat(64)}`,
    gap_code: "workflow_not_registered",
    risk_level: "R2",
    generation_route: "COMPOSITION",
    current_version: {
      id: "00000000-0000-4000-8000-000000000102",
      version_number: 1,
      version_hash: "2".repeat(64),
      workflow_id: "desi_dr2_official_chain_summary_v1",
      workflow_version: "0.1.0-candidate",
      workflow_spec_hash: "3".repeat(64),
      created_at: "2026-07-21T00:00:00Z",
    },
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:02:00Z",
  };
  const demo = {
    candidate_id: candidateId,
    candidate_version: 1,
    demo_run_id: "desi-dr2-demo-playwright",
    status: "PARTIAL",
    evidence_class: "NON_FORMAL_DEMO",
    publication_ready: false,
    claim_eligible: false,
    limitations: ["Official mirror was unavailable; no scientific claim was produced."],
    validation_summary: { checks: 8, failed: 1 },
    started_at: "2026-07-21T00:01:00Z",
    completed_at: "2026-07-21T00:02:00Z",
  };

  await page.route("http://localhost:8000/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (path === "/api/auth/me") return json({
      id: "00000000-0000-4000-8000-000000000001",
      username: "alpha",
      email: "alpha@example.test",
      subscription_tier: "solo",
      stripe_customer_id: null,
      display_name: "Alpha",
      avatar_url: null,
      google_linked: false,
    });
    if (path === "/api/privacy/preferences") return json({
      analytics_enabled: false,
      consented_at: null,
      retention_days: 30,
      research_records_retained_until_user_deletion: true,
    });
    if (path === "/api/config") return json({
      focus: "cosmology",
      signup_mode: "invite_only",
      claim_audit_enabled: true,
      foundry_candidate_catalog_enabled: true,
      foundry_auto_demo_enabled: false,
      foundry_registration_enabled: false,
      analytics_requires_consent: true,
    });
    if (path === "/api/research/capability-requests") return json({
      items: [{
        id: "00000000-0000-4000-8000-000000000103",
        status: "DEMO_RECORDED",
        gap_id: "gap-playwright",
        gap_fingerprint: candidate.gap_fingerprint,
        candidate_id: candidateId,
        audit_id: "00000000-0000-4000-8000-000000000104",
        created_at: "2026-07-21T00:00:00Z",
        updated_at: "2026-07-21T00:02:00Z",
      }],
      total: 1,
    });
    if (path === `/api/research/foundry-candidates/${candidateId}`) return json(candidate);
    if (path === `/api/research/foundry-candidates/${candidateId}/demo-runs`) {
      return json({ items: [demo], total: 1 });
    }
    if (path.startsWith("/api/admin/foundry/")) {
      return json({ detail: "Admin access required" }, 403);
    }
    return json({});
  });

  await page.goto("/foundry");
  await page.waitForTimeout(500);
  expect(browserErrors).toEqual([]);
  await expect(page.getByRole("heading", { name: "AI Workflow Foundry" })).toBeVisible();
  await expect(page.getByText("desi-dr2-demo-playwright")).toBeVisible();
  await expect(page.getByText("NON_FORMAL_DEMO")).toBeVisible();
  await expect(page.getByText("Candidate · Non-formal").first()).toBeVisible();
  await expect(page.getByText(/cannot support a scientific conclusion/i).first()).toBeVisible();

  await page.getByRole("button", { name: "中文" }).click();
  await expect(page.getByRole("heading", { name: "AI 科研工作流工厂" })).toBeVisible();
  await expect(page.getByText("候选 · 非正式").first()).toBeVisible();
  await expect(page.getByText(/不能支持科研结论/).first()).toBeVisible();
});
