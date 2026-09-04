import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import MarkdownText from "../components/chat/MarkdownText";

/**
 * Audit reason codes are machine-readable identifiers full of underscores.
 * Treating an intraword underscore as emphasis silently rewrote
 * `random_seed_mismatch` to `randomseedmismatch`, destroying the exact code
 * the human-review checklist exists to expose. CommonMark does not open
 * emphasis on an underscore flanked by word characters.
 */
describe("underscores inside identifiers", () => {
  it("keeps every underscore in a reason code", () => {
    render(<MarkdownText content="- [ ] random_seed_mismatch and ess_below_threshold" />);
    expect(screen.getByText(/random_seed_mismatch/)).toBeTruthy();
    expect(screen.getByText(/ess_below_threshold/)).toBeTruthy();
  });

  it("still renders real emphasis", () => {
    const { container } = render(<MarkdownText content="_emphasised_ and __bold__" />);
    expect(container.querySelector("em")?.textContent).toBe("emphasised");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
  });
});
