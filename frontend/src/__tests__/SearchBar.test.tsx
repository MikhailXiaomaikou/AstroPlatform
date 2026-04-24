/**
 * SearchBar — Provenance v2 rollout: non-v2 connectors must be
 * rendered as disabled + marked "维护中", unclickable.  Only vizier /
 * gaia / simbad / ned / 2mass / alma are selectable.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

vi.mock("../i18n", () => ({
  useI18n: () => ({ t: (k: string) => k }),
}));

vi.mock("../api/client", () => ({
  getSpectralLines: vi.fn(() => Promise.resolve([])),
}));

import SearchBar from "../pages/DataBrowser/SearchBar";

const V2_SOURCES = ["vizier", "gaia", "simbad", "ned", "2mass", "alma"];
const GATED_SOURCES = [
  "sdss",
  "mast",
  "chandra",
  "allwise",
  "eso",
  "irsa",
  "jwst",
  "lamost",
  "desi",
];

function renderBar() {
  return render(
    <SearchBar onSearch={vi.fn()} onAdvancedSearch={vi.fn()} loading={false} />,
  );
}

describe("SearchBar provenance-v2 source gate", () => {
  it("renders v2 sources as enabled buttons", () => {
    const { container } = renderBar();
    for (const s of V2_SOURCES) {
      const buttons = container.querySelectorAll("button.segment-btn");
      const btn = Array.from(buttons).find((b) =>
        b.textContent?.toUpperCase().startsWith(s.toUpperCase()),
      ) as HTMLButtonElement | undefined;
      expect(btn, `expected ${s} chip present`).toBeDefined();
      expect(btn!.disabled).toBe(false);
      expect(btn!.className).not.toContain("maintenance");
    }
  });

  it("renders gated (non-v2) sources as disabled and marked 维护中", () => {
    const { container } = renderBar();
    for (const s of GATED_SOURCES) {
      const buttons = container.querySelectorAll("button.segment-btn");
      const btn = Array.from(buttons).find((b) =>
        b.textContent?.toUpperCase().startsWith(s.toUpperCase()),
      ) as HTMLButtonElement | undefined;
      expect(btn, `expected ${s} chip rendered`).toBeDefined();
      expect(btn!.disabled).toBe(true);
      expect(btn!.className).toContain("maintenance");
      expect(btn!.textContent).toContain("维护中");
      expect(btn!.getAttribute("title") || "").toMatch(/under maintenance/i);
    }
  });

  it("clicking a gated source does not toggle it active", () => {
    const { container } = renderBar();
    const buttons = container.querySelectorAll("button.segment-btn");
    const sdssBtn = Array.from(buttons).find((b) =>
      b.textContent?.toUpperCase().startsWith("SDSS"),
    ) as HTMLButtonElement | undefined;
    expect(sdssBtn).toBeDefined();
    // Even if a user/AI programmatically dispatches click, the toggle must
    // early-return because disabled is guarded twice (DOM + JS).
    fireEvent.click(sdssBtn!);
    expect(sdssBtn!.className).not.toContain("active");
  });
});
