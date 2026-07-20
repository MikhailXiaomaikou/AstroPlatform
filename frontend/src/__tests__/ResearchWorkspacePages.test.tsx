import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "../i18n";

const api = vi.hoisted(() => ({
  archiveResearchWorkspace: vi.fn(),
  cancelClaimAudit: vi.fn(),
  createResearchWorkspace: vi.fn(),
  createSourceDocument: vi.fn(),
  createWorkerEnrollment: vi.fn(),
  createWorkspaceClaimAudit: vi.fn(),
  downloadEvidencePack: vi.fn(),
  getResearchWorkspace: vi.fn(),
  getRuntimeConfig: vi.fn(),
  getSourceDocumentContent: vi.fn(),
  getSourceDocumentTables: vi.fn(),
  listScientificReviewQueue: vi.fn(),
  listResearchWorkspaces: vi.fn(),
  listSourceDocuments: vi.fn(),
  listWorkerNodes: vi.fn(),
  listWorkspaceClaimAudits: vi.fn(),
  retryClaimAudit: vi.fn(),
  retrySourceDocument: vi.fn(),
  revokeWorkerNode: vi.fn(),
  submitClaimAuditReview: vi.fn(),
  verifyEvidencePack: vi.fn(),
}));
const auth = vi.hoisted(() => ({
  user: { id: "user-1", username: "alpha-user", email: "alpha@example.test" },
}));

vi.mock("../api/client", () => api);
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    loading: false,
  }),
}));

import ResearchPage from "../pages/Research/ResearchPage";
import ResearchWorkspacePage from "../pages/Research/ResearchWorkspacePage";

const workspace = {
  workspace_id: "11111111-1111-4111-8111-111111111111",
  title: "Union3 reproduction",
  description: "Reproduce the published SNe-only interval.",
  status: "ACTIVE" as const,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
};

const candidate = {
  candidate_id: `sha256:${"c".repeat(64)}`,
  candidate_hash: "c".repeat(64),
  claim_hash: "d".repeat(64),
  candidate_type: "parameter_interval_report",
  claim_text: "Union3 Table 9 reports Ωm = 0.356 +0.028/-0.026 for the SNe-only Flat ΛCDM fit.",
  parameter: "omegam",
  reported_value: {
    central: "0.356",
    plus: "0.028",
    minus: "0.026",
    lower: "0.330",
    upper: "0.384",
    confidence_level: "0.683",
  },
  model_scope: "flat_lcdm",
  data_scope: "union3_sn_only",
  interval_kind: "frequentist_profile_chi_square" as const,
  statistical_semantics: "frequentist_profile_chi_square" as const,
  confidence_definition: "delta_chi_square_1_one_parameter",
  delta_chi_square: "1",
  claim_scope: "paper_reported_frequentist_interval",
  publication_ready: false as const,
  review_required: true,
  source_anchor_ids: [`sha256:${"a".repeat(64)}`],
};

const sourceAnchor = {
  anchor_id: candidate.source_anchor_ids[0],
  source_document_hash: "b".repeat(64),
  locator: {
    section_label: "5.3",
    pdf_page_label: "58",
    table_label: "Table 9",
    role: "table_row",
  },
  raw_text: "Flat ΛCDM | SNe | 24.0 (20) | Ωm = 0.356 +0.028/-0.026",
};

const source = {
  source_document_id: "22222222-2222-4222-8222-222222222222",
  workspace_id: workspace.workspace_id,
  supersedes_source_document_id: null,
  source_profile_key: "union3_arxiv_v1",
  requested_identifier: "2311.12098v4",
  canonical_identifier: "2311.12098v4",
  version: 1,
  source_url: "https://arxiv.org/pdf/2311.12098v4",
  source_document_hash: "b".repeat(64),
  raw_artifacts: [],
  raw_artifact_hashes: { pdf: "a".repeat(64) },
  lifecycle_status: "COMPLETED",
  coverage_status: "UNION3_TABLE9_INTERVAL_READY",
  source_metadata: {},
  error: null,
  error_class: null,
  extraction: {
    source_extraction_id: "33333333-3333-4333-8333-333333333333",
    schema_version: "union3_source_extraction_v1",
    reader_version: "union3_arxiv_pdf_table9_reader_v1",
    input_source_document_hash: "b".repeat(64),
    extraction_payload: {
      schema_version: "union3_source_extraction_v1",
      reader_version: "union3_arxiv_pdf_table9_reader_v1",
      source: {},
      anchors: [sourceAnchor],
      candidates: [candidate],
      coverage_status: "UNION3_TABLE9_INTERVAL_READY",
      limitations: [],
      publication_ready: false as const,
      review_required: true,
      extraction_hash: "e".repeat(64),
    },
    extraction_payload_hash: "e".repeat(64),
    extraction_artifacts: [],
    extraction_artifact_hashes: {},
    created_at: "2026-07-20T00:00:01Z",
  },
  created_at: "2026-07-20T00:00:01Z",
};

const audit = {
  audit_id: "44444444-4444-4444-8444-444444444444",
  request_hash: "f".repeat(64),
  lifecycle_status: "QUEUED" as const,
  scientific_verdict: "WITHHELD" as const,
  mode: "execute_registered" as const,
  claim_text: candidate.claim_text,
  source: { kind: "arxiv" as const, value: "2311.12098v4" },
  evidence_input_refs: [],
  dataset_hints: [],
  normalized_claims: [],
  capability_gaps: [],
  evidence_record_ids: [],
  child_job_ids: [],
  evidence_graph: null,
  fact_check_report: null,
  error: null,
  error_class: null,
  retry_count: 0,
  evidence_pack: null,
  created_at: "2026-07-20T00:00:02Z",
  started_at: null,
  completed_at: null,
  can_cancel: true,
  can_retry: false,
  workspace_id: workspace.workspace_id,
  review_status: "PENDING" as const,
};

const enabledConfig = {
  focus: "cosmology",
  signup_mode: "invite_only" as const,
  claim_audit_enabled: true,
  research_workspace_enabled: true,
  arxiv_reader_enabled: true,
  union3_reproduction_enabled: true,
  evidence_pack_v2_enabled: true,
  local_science_worker_enabled: true,
  analytics_requires_consent: true,
};

function renderIndex() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/research"]}>
        <Routes>
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/research/workspaces/:workspaceId" element={<div>workspace detail route</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

function renderWorkspace() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[`/research/workspaces/${workspace.workspace_id}`]}>
        <Routes>
          <Route path="/research/workspaces/:workspaceId" element={<ResearchWorkspacePage />} />
          <Route path="/research" element={<div>research index route</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("Research workspace pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("astro_lang", "en");
    api.getRuntimeConfig.mockResolvedValue(enabledConfig);
    api.listResearchWorkspaces.mockResolvedValue({ items: [workspace] });
    api.createResearchWorkspace.mockResolvedValue(workspace);
    api.getResearchWorkspace.mockResolvedValue(workspace);
    api.listSourceDocuments.mockResolvedValue({ items: [source] });
    api.listWorkspaceClaimAudits.mockResolvedValue({ items: [] });
    api.listWorkerNodes.mockResolvedValue({ nodes: [] });
    api.listScientificReviewQueue.mockRejectedValue(
      Object.assign(new Error("reviewer only"), { response: { status: 403 } }),
    );
    api.createSourceDocument.mockResolvedValue(source);
    api.createWorkspaceClaimAudit.mockResolvedValue(audit);
    api.getSourceDocumentContent.mockResolvedValue({
      source_document_id: source.source_document_id,
      canonical_identifier: source.canonical_identifier,
      source_document_hash: source.source_document_hash,
      content_kind: "registered_anchors",
      anchors: [sourceAnchor],
      limitations: ["One registered Table 9 interval only."],
    });
    api.getSourceDocumentTables.mockResolvedValue({
      source_document_id: source.source_document_id,
      table_label: "Table 9",
      section_label: "5.3",
      pdf_page_label: "58",
      statistical_semantics: "frequentist_profile_chi_square",
      candidates: [candidate],
      extraction: source.extraction,
    });
    api.createWorkerEnrollment.mockResolvedValue({
      enrollment_id: "55555555-5555-4555-8555-555555555555",
      enrollment_code: "enroll_1234567890abcdefghijklmnopqrstuvwxyz",
      expires_at: "2026-07-20T00:10:00Z",
      display_once: true,
    });
    api.revokeWorkerNode.mockResolvedValue(undefined);
    api.submitClaimAuditReview.mockResolvedValue({ review_id: "review-1" });
  });

  it("keeps the research index dark when the server flag is off", async () => {
    api.getRuntimeConfig.mockResolvedValueOnce({
      ...enabledConfig,
      research_workspace_enabled: false,
    });
    renderIndex();

    expect(await screen.findByText("Research Workspaces are not open yet")).toBeInTheDocument();
    expect(api.listResearchWorkspaces).not.toHaveBeenCalled();
  });

  it("lists private workspaces and creates one from ordinary text fields", async () => {
    renderIndex();

    expect(await screen.findByText(workspace.title)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "A second reproduction" },
    });
    fireEvent.change(screen.getByLabelText("Research question"), {
      target: { value: "Can the interval be reproduced?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));

    await waitFor(() => {
      expect(api.createResearchWorkspace).toHaveBeenCalledWith({
        title: "A second reproduction",
        description: "Can the interval be reproduced?",
      });
    });
    expect(await screen.findByText("workspace detail route")).toBeInTheDocument();
  });

  it("shows all five tabs and adds only the fixed Union3 source", async () => {
    renderWorkspace();

    expect(await screen.findByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Sources" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Claims" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Evidence Packs" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add Union3 v4" }));

    await waitFor(() => {
      expect(api.createSourceDocument).toHaveBeenCalledWith(workspace.workspace_id, {
        source_profile_key: "union3_arxiv_v1",
        identifier: "2311.12098v4",
      });
    });
  });

  it("starts a registered workflow without exposing technical execution inputs", async () => {
    renderWorkspace();
    await screen.findByRole("tab", { name: "Claims" });
    fireEvent.click(screen.getByRole("tab", { name: "Claims" }));

    expect(screen.queryByLabelText(/job id/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/dataset key/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/tool parameter/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/anchor/i)).not.toBeInTheDocument();
    expect(screen.getByText(/not a posterior interval/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Start registered reproduction" }));
    await waitFor(() => {
      expect(api.createWorkspaceClaimAudit).toHaveBeenCalledWith(workspace.workspace_id, {
        source_document_id: source.source_document_id,
        candidate_id: candidate.candidate_id,
        workflow_key: "union3_flat_lcdm_sn_only_v1",
      });
    });
    expect(await screen.findByRole("tab", { name: "Runs" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps registered execution closed until every required feature gate is enabled", async () => {
    api.getRuntimeConfig.mockResolvedValueOnce({
      ...enabledConfig,
      evidence_pack_v2_enabled: false,
    });
    renderWorkspace();
    await screen.findByRole("tab", { name: "Claims" });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Refresh" })).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole("tab", { name: "Claims" }));

    expect(screen.getByText("Registered execution is off")).toBeInTheDocument();
    const startButton = screen.getByRole("button", { name: "Start registered reproduction" });
    expect(startButton).toBeDisabled();
    expect(api.createWorkspaceClaimAudit).not.toHaveBeenCalled();
  });

  it("also keeps execution closed when local science workers are disabled", async () => {
    api.getRuntimeConfig.mockResolvedValueOnce({
      ...enabledConfig,
      local_science_worker_enabled: false,
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("tab", { name: "Claims" }));

    expect(screen.getByText("Registered execution is off")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start registered reproduction" })).toBeDisabled();
    expect(api.listWorkerNodes).not.toHaveBeenCalled();
  });

  it("reads the registered Table 9 anchors and their frequentist semantics", async () => {
    renderWorkspace();
    fireEvent.click(await screen.findByRole("tab", { name: "Sources" }));
    fireEvent.click(screen.getByRole("button", { name: "Read Table 9 anchors" }));

    expect(await screen.findByText(sourceAnchor.raw_text)).toBeInTheDocument();
    expect(screen.getByText(/frequentist profile-χ² interval/i)).toBeInTheDocument();
    expect(screen.getByText("One registered Table 9 interval only.")).toBeInTheDocument();
    expect(api.getSourceDocumentContent).toHaveBeenCalledWith(source.source_document_id);
    expect(api.getSourceDocumentTables).toHaveBeenCalledWith(source.source_document_id);
  });

  it("creates a display-once Worker enrollment and revokes an owned node", async () => {
    const node = {
      node_id: "66666666-6666-4666-8666-666666666666",
      name: "My Mac",
      status: "ACTIVE",
      online: true,
      protocol_version: "compute-v1",
      public_key_fingerprint: "sha256:abc",
      capabilities: {},
      release_manifest: {},
      last_seen_at: "2026-07-20T00:00:00Z",
      created_at: "2026-07-20T00:00:00Z",
      revoked_at: null,
    };
    api.listWorkerNodes
      .mockResolvedValueOnce({ nodes: [node] })
      .mockResolvedValueOnce({ nodes: [{ ...node, status: "REVOKED", online: false }] });
    renderWorkspace();

    expect(await screen.findByText("My Mac")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create enrollment code" }));
    const code = await screen.findByText("enroll_1234567890abcdefghijklmnopqrstuvwxyz");
    expect(code).toBeInTheDocument();
    expect(screen.getAllByText("enroll_1234567890abcdefghijklmnopqrstuvwxyz")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(api.revokeWorkerNode).toHaveBeenCalledWith(node.node_id));
  });

  it("cancels a queued run and retries a retryable run through the Audit API", async () => {
    const failedAudit = {
      ...audit,
      audit_id: "77777777-7777-4777-8777-777777777777",
      lifecycle_status: "FAILED_RETRYABLE" as const,
      can_cancel: false,
      can_retry: true,
    };
    api.listWorkspaceClaimAudits.mockResolvedValue({ items: [audit, failedAudit] });
    api.cancelClaimAudit.mockResolvedValue({
      ...audit,
      lifecycle_status: "CANCELLED",
      can_cancel: false,
    });
    api.retryClaimAudit.mockResolvedValue({
      ...failedAudit,
      lifecycle_status: "QUEUED",
      can_retry: false,
      can_cancel: true,
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("tab", { name: "Runs" }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    await waitFor(() => expect(api.cancelClaimAudit).toHaveBeenCalledWith(audit.audit_id));
    fireEvent.click(screen.getByRole("button", { name: "Retry run" }));
    await waitFor(() => expect(api.retryClaimAudit).toHaveBeenCalledWith(failedAudit.audit_id));
  });

  it("shows exact review evidence and submits only the server-provided binding", async () => {
    const reviewBinding = {
      source_document_id: source.source_document_id,
      source_extraction_id: source.extraction.source_extraction_id,
      candidate_id: candidate.candidate_id,
      claim_hash: candidate.claim_hash,
      source_hash: source.source_document_hash,
      anchor_ids: candidate.source_anchor_ids,
    };
    const reviewAudit = {
      ...audit,
      lifecycle_status: "COMPLETED" as const,
      machine_support_eligible: true,
      source_document_id: source.source_document_id,
      source_extraction_id: source.extraction.source_extraction_id,
      candidate_id: candidate.candidate_id,
      normalized_claims: [candidate],
      review_binding: reviewBinding,
      review_evidence: {
        canonical_identifier: source.canonical_identifier,
        source_url: source.source_url,
        source_document_hash: source.source_document_hash,
        source_extraction_hash: source.extraction.extraction_payload_hash,
        candidate,
        anchors: [sourceAnchor],
        limitations: ["Independent review cannot override a failed machine gate."],
      },
    };
    api.listScientificReviewQueue.mockResolvedValue({ items: [reviewAudit] });
    renderWorkspace();

    expect(await screen.findByText("Review queue")).toBeInTheDocument();
    expect(screen.getByText(sourceAnchor.raw_text)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Review note"), {
      target: { value: "Checked the fixed source anchors." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve independent review" }));

    await waitFor(() => {
      expect(api.submitClaimAuditReview).toHaveBeenCalledWith(reviewAudit.audit_id, {
        ...reviewBinding,
        decision: "APPROVED",
        comment: "Checked the fixed source anchors.",
      });
    });
  });

  it("locks review approval when the immutable review packet is incomplete", async () => {
    api.listScientificReviewQueue.mockResolvedValue({ items: [{ ...audit, lifecycle_status: "COMPLETED" }] });
    renderWorkspace();

    expect(await screen.findByText(/server did not provide the complete immutable review binding/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve independent review" })).toBeDisabled();
    expect(api.submitClaimAuditReview).not.toHaveBeenCalled();
  });

  it("hides the scientific review controls from non-reviewer accounts", async () => {
    renderWorkspace();

    await waitFor(() => expect(api.listScientificReviewQueue).toHaveBeenCalled());
    expect(screen.queryByText("Review queue")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve independent review" })).not.toBeInTheDocument();
  });

  it("polls while an asynchronous source is queued", async () => {
    const intervalSpy = vi.spyOn(window, "setInterval");
    api.listSourceDocuments.mockResolvedValue({
      items: [{ ...source, lifecycle_status: "QUEUED", extraction: null }],
    });
    const view = renderWorkspace();

    await waitFor(() => expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 5000));
    view.unmount();
    intervalSpy.mockRestore();
  });

  it("renders the workspace flow in Chinese", async () => {
    localStorage.setItem("astro_lang", "zh");
    renderWorkspace();

    expect(await screen.findByRole("tab", { name: "概览" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "来源" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "主张" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "运行" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "证据包" })).toBeInTheDocument();
    expect(screen.getByText("这是复现，不是新发现")).toBeInTheDocument();
  });
});
