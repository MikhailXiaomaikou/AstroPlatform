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

  // ── G0.2: submit-path must NOT HTML-escape single quotes ──
  //
  // Reviewer observed `POINT(&#039;ICRS&#039;,...)` in the editor display
  // and worried the submitted query would carry the entity.  The editor's
  // <pre> overlay IS supposed to HTML-escape for highlighting (line 12 of
  // highlightADQL), but the <textarea>.value MUST remain raw so that the
  // state flowing through onChange → api.post() contains real `'`.
  // Regression guard: if anyone refactors highlightADQL to also touch
  // textarea.value, this test fails.

  it("preserves raw single quotes in the textarea value (submit path)", () => {
    const onChange = vi.fn();
    const { container } = render(
      <ADQLEditor value="SELECT 'a'" onChange={onChange} />,
    );
    const textarea = container.querySelector("textarea.adql-syn-textarea") as HTMLTextAreaElement;
    // The textarea's .value is what api.post() sees via parent state
    expect(textarea.value).toBe("SELECT 'a'");
    expect(textarea.value).not.toContain("&#039;");
    expect(textarea.value).not.toContain("&apos;");
  });

  it("onChange receives raw unescaped user input", () => {
    const onChange = vi.fn();
    const { container } = render(
      <ADQLEditor value="" onChange={onChange} />,
    );
    const textarea = container.querySelector("textarea.adql-syn-textarea")!;
    const raw = "SELECT * FROM t WHERE CONTAINS(POINT('ICRS',10.0,20.0),CIRCLE('ICRS',10,20,1))=1";
    fireEvent.change(textarea, { target: { value: raw } });
    expect(onChange).toHaveBeenCalledWith(raw);
    // Explicitly: single quote must be a real apostrophe, not an entity
    const calledWith = onChange.mock.calls[0][0] as string;
    expect(calledWith).toContain("'ICRS'");
    expect(calledWith).not.toContain("&#039;");
    expect(calledWith).not.toContain("&apos;");
  });

  it("<pre> overlay renders quotes visibly and <textarea> keeps them raw", () => {
    // G0.2: lock the invariant: the overlay and the textarea diverge in
    // irrelevant ways (syntax highlighting spans are fine), but the
    // textarea value always preserves real single quotes.  The reviewer
    // saw `&#039;` appear literally in the overlay due to the old
    // highlighter splitting the HTML entity across number-regex spans —
    // that's fixed (we no longer escape `'` at all, it's safe in text).
    const { container } = render(
      <ADQLEditor value="SELECT 'x'" onChange={vi.fn()} />,
    );
    const pre = container.querySelector("pre.adql-syn-pre")!;
    const textarea = container.querySelector("textarea.adql-syn-textarea") as HTMLTextAreaElement;
    // The pre must show the quote as a real ' — NOT as the literal
    // text `&#039;`, which was the reviewer-reported display bug.
    const preText = pre.textContent || "";
    expect(preText).toContain("'x'");
    expect(preText).not.toContain("&#039;");
    // The textarea value is what gets submitted — must be raw.
    expect(textarea.value).toBe("SELECT 'x'");
  });
});
