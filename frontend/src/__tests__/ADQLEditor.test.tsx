/**
 * Tests for ADQLEditor — the syntax-highlighting ADQL editor.
 *
 * Tests cover rendering, onChange callback, SQL keyword highlighting,
 * HTML entity escaping, and empty input handling.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import ADQLEditor from "../components/ADQLEditor";

describe("ADQLEditor", () => {
  const defaultProps = {
    value: "",
    onChange: vi.fn(),
  };

  // ── Rendering ──

  it("renders textarea and pre overlay", () => {
    const { container } = render(<ADQLEditor {...defaultProps} value="SELECT 1" />);
    const textarea = container.querySelector("textarea.adql-syn-textarea");
    expect(textarea).not.toBeNull();
    const pre = container.querySelector("pre.adql-syn-pre");
    expect(pre).not.toBeNull();
  });

  // ── onChange ──

  it("calls onChange when text is typed", () => {
    const onChange = vi.fn();
    const { container } = render(<ADQLEditor {...defaultProps} onChange={onChange} />);
    const textarea = container.querySelector("textarea.adql-syn-textarea")!;
    fireEvent.change(textarea, { target: { value: "SELECT * FROM stars" } });
    expect(onChange).toHaveBeenCalledWith("SELECT * FROM stars");
  });

  // ── Syntax highlighting ──

  it("highlights SQL keywords (SELECT, FROM, WHERE)", () => {
    const { container } = render(
      <ADQLEditor {...defaultProps} value="SELECT ra, dec FROM gaiadr3.gaia_source WHERE ra > 10" />,
    );
    const pre = container.querySelector("pre.adql-syn-pre")!;
    const html = pre.innerHTML;
    // Keywords should be wrapped in spans with the keyword class
    expect(html).toContain('class="adql-hl-keyword"');
    // Verify the actual keywords are highlighted
    expect(html).toContain("SELECT");
    expect(html).toContain("FROM");
    expect(html).toContain("WHERE");
  });

  // ── HTML escaping (XSS prevention) ──

  it("escapes HTML entities in input (prevents XSS)", () => {
    const { container } = render(
      <ADQLEditor {...defaultProps} value='SELECT "<script>alert(1)</script>" FROM t' />,
    );
    const pre = container.querySelector("pre.adql-syn-pre")!;
    const html = pre.innerHTML;
    // The < and > should be escaped to &lt; and &gt;
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  // ── Empty input ──

  it("handles empty input without errors", () => {
    const { container } = render(<ADQLEditor {...defaultProps} value="" />);
    const textarea = container.querySelector("textarea.adql-syn-textarea") as HTMLTextAreaElement;
    expect(textarea).not.toBeNull();
    expect(textarea.value).toBe("");
    const pre = container.querySelector("pre.adql-syn-pre")!;
    // Pre should still render (with at least the trailing newline)
    expect(pre).not.toBeNull();
  });
});
