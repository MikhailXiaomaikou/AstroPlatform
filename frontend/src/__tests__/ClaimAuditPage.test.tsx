import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  cancelClaimAudit: vi.fn(),
  createClaimAudit: vi.fn(),
  deleteClaimAudit: vi.fn(),
  downloadEvidencePack: vi.fn(),
  getClaimAudit: vi.fn(),
  getRuntimeConfig: vi.fn(),
  listClaimAudits: vi.fn(),
  retryClaimAudit: vi.fn(),
  verifyEvidencePack: vi.fn(),
  verifyEvidencePackFile: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("../context/AuthContext", () => {
  const user = { id: "user-1", username: "alpha-user" };
  return {
    useAuth: () => ({ user, loading: false }),
  };
});

import ClaimAuditPage from "../pages/ClaimAudit/ClaimAuditPage";

const withheldAudit = {
  audit_id: "11111111-1111-4111-8111-111111111111",
  request_hash: "a".repeat(64),
  lifecycle_status: "COMPLETED" as const,
  scientific_verdict: "WITHHELD" as const,
  mode: "audit_only" as const,
  claim_text: "H0 = 70 km/s/Mpc",
  source: { kind: "doi" as const, value: "10.0000/example" },
  evidence_input_refs: [],
  dataset_hints: [],
  normalized_claims: [{
    claim_id: "claim-1",
    text: "H0 = 70 km/s/Mpc",
    verdict: "WITHHELD" as const,
    parse_coverage: "complete" as const,
    supporting_evidence_ids: [],
  }],
  capability_gaps: [],
  evidence_record_ids: [],
  child_job_ids: [],
  evidence_graph: null,
  fact_check_report: null,
  error: null,
  error_class: null,
  retry_count: 0,
  evidence_pack: {
    pack_id: "22222222-2222-4222-8222-222222222222",
    status: "FINALIZED",
    schema_version: 1,
    manifest_hash: "sha256:manifest",
    key_id: "evidence-v1",
    download_url: "/api/research/evidence-packs/222/download",
    finalized_at: "2026-07-17T00:00:00Z",
  },
  created_at: "2026-07-17T00:00:00Z",
  started_at: "2026-07-17T00:00:01Z",
  completed_at: "2026-07-17T00:00:02Z",
  can_cancel: false,
  can_retry: false,
};

function renderPage() {
  return render(
    <BrowserRouter>
      <ClaimAuditPage />
    </BrowserRouter>,
  );
}

describe("ClaimAuditPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getRuntimeConfig.mockResolvedValue({
      focus: "cosmology",
      claim_audit_enabled: true,
      signup_mode: "invite_only",
      analytics_requires_consent: true,
    });
    api.listClaimAudits.mockResolvedValue({ items: [withheldAudit], total: 1 });
    api.getClaimAudit.mockResolvedValue(withheldAudit);
    api.createClaimAudit.mockResolvedValue(withheldAudit);
    api.verifyEvidencePack.mockResolvedValue({
      valid: true,
      key_id: "evidence-v1",
      scientific_verdict: "WITHHELD",
    });
  });

  it("keeps the product visibly closed when the server flag is off", async () => {
    api.getRuntimeConfig.mockResolvedValueOnce({
      focus: "cosmology",
      claim_audit_enabled: false,
      signup_mode: "invite_only",
      analytics_requires_consent: true,
    });
    renderPage();

    expect(await screen.findByText("Claim Audit is not open yet")).toBeInTheDocument();
    expect(screen.getByText(/14-day Daily evidence gate/)).toBeInTheDocument();
    expect(api.listClaimAudits).not.toHaveBeenCalled();
  });

  it("shows lifecycle and scientific verdict separately and verifies a private pack", async () => {
    renderPage();

    await screen.findAllByText(withheldAudit.claim_text);
    expect(screen.queryByText("Supported by this run")).not.toBeInTheDocument();
    expect(screen.getAllByText("COMPLETED").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Withheld").length).toBeGreaterThan(0);
    expect(screen.getByText(/support is withheld unless every strong claim/)).toBeInTheDocument();
    expect(screen.getByText("No qualifying evidence record")).toBeInTheDocument();
    expect(screen.getByText("Full claim parsed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Verify stored pack" }));
    expect(await screen.findByText("Signature and contents verified")).toBeInTheDocument();
    expect(api.verifyEvidencePack).toHaveBeenCalledWith(withheldAudit.evidence_pack.pack_id);
  });

  it("warns when the parser did not cover the complete claim", async () => {
    const partialAudit = {
      ...withheldAudit,
      claim_text: "H0 = 70 km/s/Mpc, therefore dark energy is evolving.",
      normalized_claims: [{
        ...withheldAudit.normalized_claims[0],
        text: "H0 = 70 km/s/Mpc, therefore dark energy is evolving.",
        parse_coverage: "unparsed_residual" as const,
      }],
    };
    api.listClaimAudits.mockResolvedValueOnce({ items: [partialAudit], total: 1 });
    renderPage();

    expect(await screen.findByText("Unparsed wording remains")).toBeInTheDocument();
    expect(screen.getByText(/residual wording was not evaluated/)).toBeInTheDocument();
  });

  it("submits only the explicit mode, source, registry hints, and signed job ids", async () => {
    renderPage();
    await screen.findByText("Research history");

    fireEvent.change(screen.getByLabelText("Scientific claim"), {
      target: { value: "DESI DR2 proves evolving dark energy." },
    });
    fireEvent.change(screen.getByLabelText("Source identifier"), {
      target: { value: "10.1103/tr6y-kpc6" },
    });
    fireEvent.change(screen.getByLabelText("Mode"), {
      target: { value: "execute_registered" },
    });
    fireEvent.click(screen.getByText("Registered evidence inputs"));
    fireEvent.change(screen.getByLabelText(/Research job IDs/), {
      target: { value: "job-1\njob-1" },
    });
    fireEvent.change(screen.getByLabelText(/Dataset registry keys/), {
      target: { value: "desi_dr2_bao" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Claim Audit" }));

    await waitFor(() => {
      expect(api.createClaimAudit).toHaveBeenCalledWith({
        claim_text: "DESI DR2 proves evolving dark energy.",
        source: { kind: "doi", value: "10.1103/tr6y-kpc6" },
        evidence_input_refs: ["job-1"],
        dataset_hints: ["desi_dr2_bao"],
        mode: "execute_registered",
      });
    });
  });
});
