/**
 * Regression tests for the Help page.
 *
 * M3 (2026-05-18) deleted the Data Browser, ADQL, Pipeline and Workspace
 * pages (see src/App.tsx). The in-app Help must not instruct users to open
 * tabs, pages, or editor shortcuts that no longer exist anywhere in the app.
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import HelpPage from "../pages/Help/HelpPage";

describe("HelpPage does not document M3-deleted surfaces", () => {
  it("Quick Start / Feature Overview does not mention the deleted Data Browser or Pipeline pages", () => {
    render(<HelpPage />);
    // Quick Start is the default tab and contains the Feature Overview cards.
    expect(screen.queryByText(/Data Browser/i)).toBeNull();
    expect(screen.queryByText(/Open in Pipeline/i)).toBeNull();
    expect(screen.queryByText(/Build processing graphs visually/i)).toBeNull();
  });

  it("FAQ does not reference Data Browser tabs or pipeline inputs", () => {
    render(<HelpPage />);
    fireEvent.click(screen.getByRole("button", { name: "FAQ" }));
    // Question list (always visible) must not advertise the deleted
    // Quick Search / Advanced Search pages.
    expect(screen.queryByText(/Quick Search/i)).toBeNull();
    // The accordion shows one answer at a time — open each and re-check.
    const questions = Array.from(document.querySelectorAll("button")).filter(
      (b) => b.textContent?.includes("▾"),
    );
    expect(questions.length).toBeGreaterThan(0);
    for (const q of questions) {
      fireEvent.click(q);
      expect(screen.queryByText(/Data Browser/i)).toBeNull();
      expect(screen.queryByText(/pipeline inputs/i)).toBeNull();
    }
  });

  it("Keyboard shortcuts do not list Pipeline-editor bindings", () => {
    render(<HelpPage />);
    fireEvent.click(screen.getByRole("button", { name: "Keyboard Shortcuts" }));
    expect(screen.queryByText(/Pipeline editor/i)).toBeNull();
  });
});
