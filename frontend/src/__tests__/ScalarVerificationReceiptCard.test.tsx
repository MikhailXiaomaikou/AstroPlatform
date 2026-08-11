import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../i18n", () => ({
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

import { AutoToolResult } from "../pages/Chat/AutoToolResult";

function receipt(sourceStatus = "verified_exact") {
  return {
    success: true,
    schema_version: 1,
    response_disposition: sourceStatus === "verified_exact" ? "full" : "limited",
    source_status: sourceStatus,
    operation: "ratio",
    result: {
      value: 0.891852994,
      standard_uncertainty: 0.020562805,
      unit: "dimensionless",
      rounded_display: "0.891853 ± 0.020563",
    },
    formula: "q0 / q1",
    uncertainty_model: {
      kind: "correlation_matrix",
      matrix: [[1, -0.404], [-0.404, 1]],
    },
    inputs: [
      {
        id: "D_M",
        label: "D_M/r_d",
        value: 17.351,
        standard_uncertainty: 0.177,
        unit: "dimensionless",
        source_locator: "Table 4, LRG2",
      },
      {
        id: "D_H",
        label: "D_H/r_d",
        value: 19.455,
        standard_uncertainty: 0.330,
        unit: "dimensionless",
        source_locator: "Table 4, LRG2",
      },
    ],
    source_evidence: [
      {
        id: "desi",
        kind: "arxiv",
        identifier: "2503.14738",
        locator: "Table 4, LRG2",
        status: sourceStatus,
        extraction_method: "ar5iv_html",
        sha256: "a".repeat(64),
        cache_hit: true,
      },
    ],
    assumptions: ["Correlation was taken from the same table row."],
    boundary_statement: "This is a table consistency calculation, not a BAO likelihood fit.",
    receipt_sha256: "b".repeat(64),
  };
}

describe("ScalarVerificationReceiptCard", () => {
  it("shows a compact full receipt with result, source locator, method, hash, and boundary", () => {
    render(<AutoToolResult toolName="verify_scalar_derivation" result={receipt()} />);

    expect(screen.getByTestId("scalar-verification-receipt")).toBeTruthy();
    expect(screen.getByText("0.891853 ± 0.020563")).toBeTruthy();
    expect(screen.getAllByText(/Table 4, LRG2/).length).toBeGreaterThan(0);
    expect(screen.getByText(/ar5iv_html/)).toBeTruthy();
    expect(screen.getByText(/SHA-256 a{20}…/)).toBeTruthy();
    expect(screen.getByText(/not a BAO likelihood fit/)).toBeTruthy();
    expect(screen.getByText("chat.scalar_receipt.source_verified")).toBeTruthy();
  });

  it("uses a prominent conflict state while retaining the supplied-input calculation", () => {
    render(<AutoToolResult toolName="verify_scalar_derivation" result={receipt("conflict")} />);

    expect(screen.getByText("0.891853 ± 0.020563")).toBeTruthy();
    expect(screen.getByText("chat.scalar_receipt.source_conflict")).toBeTruthy();
    expect(screen.getByText(/\(conflict\)/)).toBeTruthy();
  });

  it("renders an abstention receipt before the generic error branch", () => {
    render(
      <AutoToolResult
        toolName="verify_scalar_derivation"
        result={{
          success: false,
          response_disposition: "abstention",
          source_status: "unavailable",
          error: "Correlation matrix is missing.",
          missing_dependencies: ["uncertainty_model"],
          source_evidence: [],
          inputs: [],
          assumptions: [],
          boundary_statement: "No calculation was released.",
        }}
      />,
    );

    expect(screen.getByTestId("scalar-verification-receipt")).toBeTruthy();
    expect(screen.getByText("Correlation matrix is missing.")).toBeTruthy();
    expect(screen.queryByText(/^Error:/)).toBeNull();
  });
});
