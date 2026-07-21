import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider, useI18n } from "../i18n";

const api = vi.hoisted(() => ({
  getAdminFoundryCandidate: vi.fn(),
  getFoundryCandidate: vi.fn(),
  getRuntimeConfig: vi.fn(),
  listAdminFoundryRequests: vi.fn(),
  listCapabilityRequests: vi.fn(),
  listFoundryDemoRuns: vi.fn(),
  registerAdminFoundryCandidate: vi.fn(),
  reviewAdminFoundryCandidate: vi.fn(),
  revokeAdminFoundryCandidate: vi.fn(),
  suspendAdminFoundryCandidate: vi.fn(),
  triageAdminFoundryRequest: vi.fn(),
  validateAdminFoundryCandidate: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "user-1", username: "alpha", email: "alpha@example.test" },
    loading: false,
  }),
}));

import FoundryPage from "../pages/Foundry/FoundryPage";

const version = {
  id: "version-1",
  version_number: 1,
  version_hash: "a".repeat(64),
  workflow_id: "desi_dr2_chain_summary_v1",
  workflow_version: "0.1.0-candidate",
  workflow_spec_hash: "b".repeat(64),
  created_at: "2026-07-21T00:00:00Z",
};

const demo = {
  candidate_id: "candidate-1",
  candidate_version: 1,
  demo_run_id: "demo-1",
  status: "PASSED",
  evidence_class: "NON_FORMAL_DEMO" as const,
  publication_ready: false as const,
  claim_eligible: false as const,
  limitations: ["Candidate Demo only."],
  validation_summary: { checks: 12, failed: 0 },
  started_at: "2026-07-21T00:01:00Z",
  completed_at: "2026-07-21T00:02:00Z",
};

const candidate = {
  id: "candidate-1",
  status: "DEMO_RECORDED",
  gap_fingerprint: "sha256:gap-fingerprint",
  gap_code: "workflow_not_registered",
  risk_level: "R2",
  generation_route: "COMPOSITION",
  current_version: version,
  demo_runs: [demo],
  created_at: "2026-07-21T00:00:00Z",
  updated_at: "2026-07-21T00:02:00Z",
};

const request = {
  id: "request-1",
  status: "DEMO_RECORDED",
  gap_id: "gap-1",
  gap_fingerprint: "sha256:gap-fingerprint",
  candidate_id: candidate.id,
  audit_id: "audit-1",
  created_at: "2026-07-21T00:00:00Z",
  updated_at: "2026-07-21T00:02:00Z",
};

function LanguageToggle() {
  const { setLang } = useI18n();
  return <button onClick={() => setLang("zh")}>switch-zh</button>;
}

function renderPage(withToggle = false) {
  return render(
    <I18nProvider>
      <MemoryRouter>
        {withToggle && <LanguageToggle />}
        <FoundryPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("Workflow Foundry page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("astro_lang", "en");
    api.getRuntimeConfig.mockResolvedValue({
      foundry_candidate_catalog_enabled: true,
      foundry_auto_demo_enabled: true,
      foundry_registration_enabled: true,
    });
    api.listCapabilityRequests.mockResolvedValue({ items: [request], total: 1 });
    api.getFoundryCandidate.mockResolvedValue(candidate);
    api.listFoundryDemoRuns.mockResolvedValue({ items: [demo], total: 1 });
    api.listAdminFoundryRequests.mockRejectedValue(
      Object.assign(new Error("reviewer only"), { response: { status: 403 } }),
    );
    api.getAdminFoundryCandidate.mockResolvedValue(candidate);
    api.validateAdminFoundryCandidate.mockResolvedValue({
      validation_run_id: "validation-1",
      status: "QUEUED",
      candidate_id: candidate.id,
      candidate_version_id: version.id,
      candidate_version_hash: version.version_hash,
      created_at: "2026-07-21T00:03:00Z",
    });
    api.reviewAdminFoundryCandidate.mockResolvedValue(candidate);
    api.triageAdminFoundryRequest.mockResolvedValue(request);
    api.registerAdminFoundryCandidate.mockResolvedValue(candidate);
    api.suspendAdminFoundryCandidate.mockResolvedValue(candidate);
    api.revokeAdminFoundryCandidate.mockResolvedValue(candidate);
  });

  it("shows only owned requests and marks every recorded Demo as non-formal", async () => {
    renderPage();

    expect(await screen.findByText("AI Workflow Foundry")).toBeInTheDocument();
    expect(await screen.findAllByText("sha256:gap-fingerprint")).not.toHaveLength(0);
    expect(await screen.findByText("demo-1")).toBeInTheDocument();
    expect(screen.getAllByText("Candidate · Non-formal").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/cannot support a scientific conclusion/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("NON_FORMAL_DEMO")).toBeInTheDocument();
    expect(screen.queryByText("Foundry Console")).not.toBeInTheDocument();
    expect(api.getFoundryCandidate).toHaveBeenCalledWith(candidate.id);
  });

  it("switches the candidate boundary and catalog headings to Chinese", async () => {
    renderPage(true);
    await screen.findByText("AI Workflow Foundry");
    fireEvent.click(screen.getByRole("button", { name: "switch-zh" }));

    expect(await screen.findByText("AI 科研工作流工厂")).toBeInTheDocument();
    expect(screen.getAllByText("候选 · 非正式").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("候选目录")).toBeInTheDocument();
  });

  it("lets a reviewer queue validation only for the exact immutable version", async () => {
    api.listAdminFoundryRequests.mockResolvedValue({ items: [request], total: 1 });
    renderPage();

    expect(await screen.findByText("Foundry Console")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start isolated validation" }));

    await waitFor(() => {
      expect(api.validateAdminFoundryCandidate).toHaveBeenCalledWith(candidate.id, {
        candidate_version_id: version.id,
        candidate_version_hash: version.version_hash,
      });
    });
    expect(api.validateAdminFoundryCandidate.mock.calls[0][1]).not.toHaveProperty("status");
    expect(api.validateAdminFoundryCandidate.mock.calls[0][1]).not.toHaveProperty("results");
  });

  it("keeps triage available when a new request already has a draft candidate", async () => {
    const draftCandidate = {
      ...candidate,
      status: "DRAFT",
      generation_route: null,
      risk_level: null,
      current_version: null,
      versions: [],
      demo_runs: [],
    };
    api.getFoundryCandidate.mockResolvedValue(draftCandidate);
    api.listFoundryDemoRuns.mockResolvedValue({ items: [], total: 0 });
    api.listAdminFoundryRequests.mockResolvedValue({
      items: [{ ...request, status: "SUBMITTED" }],
      total: 1,
    });
    api.getAdminFoundryCandidate.mockResolvedValue(draftCandidate);
    renderPage();

    const triage = await screen.findByRole("button", { name: "Accept for candidate generation" });
    fireEvent.click(triage);
    await waitFor(() => {
      expect(api.triageAdminFoundryRequest).toHaveBeenCalledWith(request.id, {
        generation_route: "COMPOSITION",
        risk_level: "R1",
      });
    });
  });

  it("binds an append-only review to both the exact version and selected scope", async () => {
    api.listAdminFoundryRequests.mockResolvedValue({ items: [request], total: 1 });
    renderPage();

    await screen.findByText("Foundry Console");
    fireEvent.change(screen.getByLabelText("Review note or required reason"), {
      target: { value: "Engineering checks passed." },
    });
    fireEvent.change(screen.getByLabelText("Review scope"), {
      target: { value: "ENGINEERING" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve exact version" }));

    await waitFor(() => {
      expect(api.reviewAdminFoundryCandidate).toHaveBeenCalledWith(candidate.id, {
        candidate_version_id: version.id,
        candidate_version_hash: version.version_hash,
        review_scope: "ENGINEERING",
        decision: "APPROVED",
        comment: "Engineering checks passed.",
      });
    });
  });

  it("requests registration only with a server-verified formal build receipt", async () => {
    const digest = `sha256:${"c".repeat(64)}`;
    const buildAttestation = {
      build_attestation_id: "build-attestation-1",
      candidate_version_id: version.id,
      candidate_version_hash: version.version_hash,
      formal_worker_image_digest: digest,
      source_tree_sha256: "d".repeat(64),
      dependency_lock_sha256: "e".repeat(64),
      formal_sbom_sha256: "f".repeat(64),
      test_report_sha256: "1".repeat(64),
      git_commit: "2".repeat(40),
      oidc_issuer: "https://token.actions.githubusercontent.com",
      oidc_subject: "repo:standard-astro/platform:environment:foundry-formal-build",
      sigstore_bundle_sha256: "3".repeat(64),
      provenance_sha256: "4".repeat(64),
      receipt_sha256: "5".repeat(64),
      built_at: "2026-07-21T00:04:00Z",
      created_at: "2026-07-21T00:04:00Z",
      status: "VERIFIED_BUILD_RECEIPT",
    };
    const approvedCandidate = {
      ...candidate,
      status: "APPROVED",
      formal_build_attestations: [buildAttestation],
    };
    api.listAdminFoundryRequests.mockResolvedValue({ items: [request], total: 1 });
    api.getAdminFoundryCandidate.mockResolvedValue(approvedCandidate);
    renderPage();

    await screen.findByText("Foundry Console");
    const register = screen.getByRole("button", { name: "Request signed registry release" });
    expect(register).not.toBeDisabled();
    fireEvent.click(register);

    await waitFor(() => {
      expect(api.registerAdminFoundryCandidate).toHaveBeenCalledWith(
        candidate.id,
        version.id,
        version.version_hash,
        buildAttestation.build_attestation_id,
      );
    });
  });

  it("does not let the browser type a formal Worker digest", async () => {
    const approvedCandidate = { ...candidate, status: "APPROVED", formal_build_attestations: [] };
    api.listAdminFoundryRequests.mockResolvedValue({ items: [request], total: 1 });
    api.getAdminFoundryCandidate.mockResolvedValue(approvedCandidate);
    renderPage();

    await screen.findByText("Foundry Console");
    expect(screen.getByRole("button", { name: "Request signed registry release" })).toBeDisabled();
    expect(screen.queryByPlaceholderText("sha256:…")).not.toBeInTheDocument();
    expect(screen.getByText("Waiting for protected CI build")).toBeInTheDocument();
  });

  it("fails closed when the candidate catalog feature flag is off", async () => {
    api.getRuntimeConfig.mockResolvedValue({ foundry_candidate_catalog_enabled: false });
    renderPage();

    expect(await screen.findByText("Candidate Catalog is not open yet")).toBeInTheDocument();
    expect(api.listCapabilityRequests).not.toHaveBeenCalled();
  });
});
