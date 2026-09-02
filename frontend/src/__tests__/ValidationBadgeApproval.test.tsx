import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import { ValidationBadge } from "../pages/Chat/ValidationBadge";
import { I18nProvider } from "../i18n";

/**
 * Chat cannot reach the human-review lane: the ClaimAuditReview rows live
 * behind three default-off flags and no loop path writes one. The badge
 * therefore states the approval state outright instead of leaving a reader to
 * infer an absent approval from an absent field.
 */
function renderBadge(summary: Record<string, unknown>) {
  return render(
    <I18nProvider>
      <ValidationBadge summary={summary as never} />
    </I18nProvider>,
  );
}

describe("ValidationBadge approval state", () => {
  it("reports 'none' with a reason when the backend sends approval_state none", () => {
    renderBadge({
      schema_version: 2,
      numeric_gate: "passed",
      citation_gate: "passed",
      response_disposition: "full",
      approval_state: "none",
    });
    expect(screen.getByText(/Human approval/i)).toBeTruthy();
    expect(screen.getByText(/no reviewer record backs this reply/i)).toBeTruthy();
  });

  it("still reports none when an older backend omits the field", () => {
    renderBadge({
      schema_version: 2,
      numeric_gate: "passed",
      citation_gate: "passed",
      response_disposition: "full",
    });
    expect(screen.getByText(/no reviewer record backs this reply/i)).toBeTruthy();
  });
});
