/**
 * Tests for the per-message validation badge (2026-07-03 honesty surfacing).
 *
 * Locks the honesty contract:
 * - passed / intervened(regenerated) / blocked / not-validated are four
 *   visually distinct states (distinct labels + data-validation-state);
 * - messages without a summary render NOTHING (old messages unchanged);
 * - a skipped/no-data gate is never presented as a pass;
 * - hit_iteration_cap renders a truncation note.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Pass i18n keys through so assertions are locale-independent.
vi.mock("../i18n", () => ({
  I18nProvider: ({ children }: { children: React.ReactNode }) => children,
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

import { ValidationBadge, overallValidationState } from "../pages/Chat/ValidationBadge";
import type { ValidationSummary } from "../api/client";

const base: ValidationSummary = {
  schema_version: 1,
  numeric_gate: "passed",
  citation_gate: "passed",
  regen_count: 0,
  blocked: false,
  interventions: [],
};

describe("overallValidationState", () => {
  it("maps gate states to the four badge states", () => {
    expect(overallValidationState(base)).toBe("passed");
    expect(overallValidationState({ ...base, regen_count: 2 })).toBe("regenerated");
    expect(overallValidationState({ ...base, numeric_gate: "regenerated" })).toBe("regenerated");
    expect(overallValidationState({ ...base, citation_gate: "limited", limited: true })).toBe("limited");
    expect(overallValidationState({ ...base, numeric_gate: "blocked", blocked: true })).toBe("blocked");
    expect(overallValidationState({ ...base, citation_gate: "blocked" })).toBe("blocked");
  });

  it("never reports a pass when the numeric gate had no data or did not run", () => {
    expect(overallValidationState({ ...base, numeric_gate: "skipped_no_data" })).toBe("not_validated");
    expect(overallValidationState({ ...base, numeric_gate: "skipped" })).toBe("not_validated");
    expect(overallValidationState({ ...base, numeric_gate: "not_run", citation_gate: "not_run" })).toBe("not_validated");
  });
});

describe("ValidationBadge rendering", () => {
  it("renders nothing when there is no summary and no truncation (old messages)", () => {
    const { container } = render(<ValidationBadge />);
    expect(container.innerHTML).toBe("");
  });

  it("renders the passed badge with understated wording", () => {
    render(<ValidationBadge summary={base} />);
    const chip = screen.getByText(/chat\.validation\.badge_passed/);
    expect(chip).toBeTruthy();
    expect(chip.getAttribute("data-validation-state")).toBe("passed");
    // Expandable detail carries the per-gate states + honesty disclaimer.
    expect(screen.getByText("chat.validation.numeric_gate:")).toBeTruthy();
    expect(screen.getByText("chat.validation.citation_gate:")).toBeTruthy();
    expect(screen.getAllByText(/chat\.validation\.tooltip/).length).toBeGreaterThan(0);
  });

  it("renders intervened (regenerated) distinctly, with regen count and interventions", () => {
    render(
      <ValidationBadge
        summary={{
          ...base,
          numeric_gate: "regenerated",
          regen_count: 2,
          interventions: [{ gate: "numeric_claims", action: "regenerated_clean", reason: "" }],
        }}
      />,
    );
    const chip = screen.getByText(/chat\.validation\.badge_regenerated/);
    expect(chip.getAttribute("data-validation-state")).toBe("regenerated");
    expect(screen.getByText(/2 regeneration|chat\.validation\.regen_count/)).toBeTruthy();
    expect(screen.getByText(/numeric_claims→regenerated_clean/)).toBeTruthy();
  });

  it("renders blocked distinctly", () => {
    render(
      <ValidationBadge
        summary={{
          ...base,
          numeric_gate: "blocked",
          blocked: true,
          interventions: [{ gate: "zero_data", action: "blocked", reason: "no_rewrite" }],
        }}
      />,
    );
    const chip = screen.getByText(/chat\.validation\.badge_blocked/);
    expect(chip.getAttribute("data-validation-state")).toBe("blocked");
    expect(screen.getByText(/zero_data→blocked/)).toBeTruthy();
  });

  it("renders a limited answer distinctly from a hard block", () => {
    render(
      <ValidationBadge
        summary={{
          ...base,
          citation_gate: "limited",
          limited: true,
          interventions: [{ gate: "citation_methodology", action: "annotated_limited", reason: "" }],
        }}
      />,
    );
    const chip = screen.getByText(/chat\.validation\.badge_limited/);
    expect(chip.getAttribute("data-validation-state")).toBe("limited");
    expect(screen.queryByText(/chat\.validation\.badge_blocked/)).toBeNull();
    expect(screen.getByText(/citation_methodology→annotated_limited/)).toBeTruthy();
  });

  it("renders not-validated (with reason) distinctly from passed", () => {
    render(
      <ValidationBadge
        summary={{
          ...base,
          numeric_gate: "not_run",
          citation_gate: "not_run",
          reason: "honest_abstention",
        }}
      />,
    );
    const chip = screen.getByText(/chat\.validation\.badge_not_validated/);
    expect(chip.getAttribute("data-validation-state")).toBe("not_validated");
    expect(chip.textContent).toContain("honest_abstention");
    expect(screen.queryByText(/chat\.validation\.badge_passed/)).toBeNull();
  });

  it("renders the truncation note for hit_iteration_cap, even without a summary", () => {
    render(<ValidationBadge truncated />);
    expect(screen.getByText(/chat\.validation\.truncated/)).toBeTruthy();
    expect(screen.queryByText(/chat\.validation\.badge_/)).toBeNull();
  });
});
