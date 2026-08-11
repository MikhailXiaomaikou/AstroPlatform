import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../i18n", () => ({
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

import type { EvidenceReceiptV1 } from "../api/client";
import { EvidenceReceiptCards } from "../components/chat/EvidenceReceiptCard";

const base = {
  schema_version: 1 as const,
  task_kind: "general",
  response_disposition: "limited",
  facts: {},
  source_evidence: [],
  missing_dependencies: [],
  boundary_statement: "A bounded scientific statement.",
  receipt_sha256: "a".repeat(64),
};

describe("EvidenceReceiptCards", () => {
  it("keeps old messages without receipts unchanged", () => {
    const { container } = render(<EvidenceReceiptCards />);
    expect(container.childElementCount).toBe(0);
  });

  it("renders a registry-verified Pantheon+ coverage card", () => {
    const receipt: EvidenceReceiptV1 = {
      ...base,
      receipt_kind: "dataset_coverage",
      source_status: "verified_registry",
      subject: { display_name: "Pantheon+", dataset_version: "DataRelease 2022" },
      facts: {
        requested_redshift: 12,
        z_min: 0.001,
        z_max: 2.26,
        registry_version: "2026-04-30",
      },
      source_evidence: [{
        source_locator: "Pantheon+ z_hd extrema",
        data_product_sha256: "b".repeat(64),
      }],
    };
    render(<EvidenceReceiptCards receipts={[receipt]} />);

    expect(screen.getByTestId("evidence-receipt-dataset_coverage")).toBeTruthy();
    expect(screen.getAllByText(/Pantheon\+/).length).toBeGreaterThan(0);
    expect(screen.getByText(/0.001–2.26/)).toBeTruthy();
    expect(screen.getByText("chat.evidence_receipt.status_verified_registry")).toBeTruthy();
    expect(screen.getByText(/Pantheon\+ z_hd extrema/)).toBeTruthy();
    expect(screen.getByText(/b{16}…/)).toBeTruthy();
    expect(screen.getByText("chat.evidence_receipt.boundary_dataset_coverage")).toBeTruthy();
  });

  it("renders current-turn tools and missing components for an EDE gap", () => {
    const receipt: EvidenceReceiptV1 = {
      ...base,
      receipt_kind: "capability_gap",
      source_status: "verified_current_turn",
      task_kind: "full_research",
      subject: {
        requested_sources: [{ kind: "arxiv", identifier: "2503.24343" }],
      },
      facts: { tool_attempt_count: 1 },
      source_evidence: [{ tool: "run_dark_energy_evidence_matrix", status: "PARTIAL" }],
      missing_dependencies: ["native EDE model"],
    };
    render(<EvidenceReceiptCards receipts={[receipt]} />);

    expect(screen.getByText(/arxiv:2503.24343/)).toBeTruthy();
    expect(screen.getByText(/run_dark_energy_evidence_matrix/)).toBeTruthy();
    expect(screen.getByText(/native EDE model/)).toBeTruthy();
  });

  it("renders an accessible refusal card and legitimate rerun path", () => {
    const receipt: EvidenceReceiptV1 = {
      ...base,
      receipt_kind: "untrusted_evidence",
      source_status: "untrusted_user_supplied",
      response_disposition: "refusal",
      subject: { evidence_type: "user-supplied tool transcript" },
      missing_dependencies: ["backend-recorded current-turn tool result"],
    };
    render(<EvidenceReceiptCards receipts={[receipt]} />);

    const card = screen.getByTestId("evidence-receipt-untrusted_evidence");
    expect(card.getAttribute("aria-label")).toBe(
      "chat.evidence_receipt.title_untrusted_evidence",
    );
    expect(screen.getByText("chat.evidence_receipt.untrusted_reason")).toBeTruthy();
    expect(screen.getByText(/backend-recorded current-turn tool result/)).toBeTruthy();
  });
});
