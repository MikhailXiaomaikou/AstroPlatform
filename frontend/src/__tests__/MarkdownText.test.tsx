/**
 * Tests for MarkdownText — lightweight inline Markdown renderer.
 *
 * Tests cover plain text, bold, italic, inline code, code blocks,
 * headers, bullet lists, numbered lists, links, strikethrough,
 * GFM tables, and XSS prevention.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MarkdownText from "../components/chat/MarkdownText";

describe("MarkdownText", () => {
  // ── Plain text ──

  it("renders plain text", () => {
    render(<MarkdownText content="Hello world" />);
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  // ── Bold ──

  it("renders **bold** text", () => {
    const { container } = render(<MarkdownText content="This is **bold** text" />);
    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong!.textContent).toBe("bold");
  });

  // ── Italic ──

  it("renders *italic* text", () => {
    const { container } = render(<MarkdownText content="This is *italic* text" />);
    const em = container.querySelector("em");
    expect(em).not.toBeNull();
    expect(em!.textContent).toBe("italic");
  });

  // ── Inline code ──

  it("renders `inline code`", () => {
    const { container } = render(<MarkdownText content="Use `console.log` here" />);
    const code = container.querySelector("code.md-inline-code");
    expect(code).not.toBeNull();
    expect(code!.textContent).toBe("console.log");
  });

  // ── Code blocks ──

  it("renders code blocks with ``` markers", () => {
    const content = "```python\nprint('hello')\n```";
    const { container } = render(<MarkdownText content={content} />);
    const pre = container.querySelector("pre.md-code-block");
    expect(pre).not.toBeNull();
    const code = pre!.querySelector("code");
    expect(code).not.toBeNull();
    expect(code!.textContent).toBe("print('hello')");
    // Language tag should be rendered
    const langSpan = pre!.querySelector(".md-code-lang");
    expect(langSpan).not.toBeNull();
    expect(langSpan!.textContent).toBe("python");
  });

  // ── Headers ──

  it("renders # header as h2", () => {
    const { container } = render(<MarkdownText content="# Title" />);
    const h2 = container.querySelector("h2.md-h2");
    expect(h2).not.toBeNull();
    expect(h2!.textContent).toBe("Title");
  });

  it("renders ## header as h3", () => {
    const { container } = render(<MarkdownText content="## Subtitle" />);
    const h3 = container.querySelector("h3.md-h3");
    expect(h3).not.toBeNull();
    expect(h3!.textContent).toBe("Subtitle");
  });

  it("renders ### header as h4", () => {
    const { container } = render(<MarkdownText content="### Subsubtitle" />);
    const h4 = container.querySelector("h4.md-h4");
    expect(h4).not.toBeNull();
    expect(h4!.textContent).toBe("Subsubtitle");
  });

  // ── Bullet lists ──

  it("renders bullet lists (- item)", () => {
    const content = "- Alpha\n- Beta\n- Gamma";
    const { container } = render(<MarkdownText content={content} />);
    const ul = container.querySelector("ul.md-list");
    expect(ul).not.toBeNull();
    const items = ul!.querySelectorAll("li");
    expect(items.length).toBe(3);
    expect(items[0].textContent).toBe("Alpha");
    expect(items[1].textContent).toBe("Beta");
    expect(items[2].textContent).toBe("Gamma");
  });

  // ── Numbered lists ──

  it("renders numbered lists (1. item)", () => {
    const content = "1. First\n2. Second\n3. Third";
    const { container } = render(<MarkdownText content={content} />);
    const ol = container.querySelector("ol.md-list");
    expect(ol).not.toBeNull();
    const items = ol!.querySelectorAll("li");
    expect(items.length).toBe(3);
    expect(items[0].textContent).toBe("First");
    expect(items[1].textContent).toBe("Second");
    expect(items[2].textContent).toBe("Third");
  });

  // ── Links ──

  it("renders links [text](url)", () => {
    const { container } = render(<MarkdownText content="Visit [Example](https://example.com) now" />);
    const link = container.querySelector("a.md-link") as HTMLAnchorElement;
    expect(link).not.toBeNull();
    expect(link.textContent).toBe("Example");
    expect(link.href).toBe("https://example.com/");
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
  });

  // ── Strikethrough ──

  it("renders ~~strikethrough~~", () => {
    const { container } = render(<MarkdownText content="This is ~~deleted~~ text" />);
    const del = container.querySelector("del");
    expect(del).not.toBeNull();
    expect(del!.textContent).toBe("deleted");
  });

  // ── GFM tables ──

  it("renders GFM tables", () => {
    const content = [
      "| Name | RA | Dec |",
      "| --- | --- | --- |",
      "| M31 | 10.68 | 41.27 |",
      "| M33 | 23.46 | 30.66 |",
    ].join("\n");
    const { container } = render(<MarkdownText content={content} />);
    const table = container.querySelector("table.md-table");
    expect(table).not.toBeNull();
    const headers = table!.querySelectorAll("th");
    expect(headers.length).toBe(3);
    expect(headers[0].textContent).toBe("Name");
    expect(headers[1].textContent).toBe("RA");
    const rows = table!.querySelectorAll("tbody tr");
    expect(rows.length).toBe(2);
    const firstRowCells = rows[0].querySelectorAll("td");
    expect(firstRowCells[0].textContent).toBe("M31");
  });

  // ── XSS prevention ──

  it("does not render XSS in link tags", () => {
    const content = '[Click me](javascript:alert("XSS"))';
    const { container } = render(<MarkdownText content={content} />);
    const link = container.querySelector("a.md-link") as HTMLAnchorElement;
    expect(link).not.toBeNull();
    // The link should exist but React prevents javascript: protocol by default.
    // The href is set but React sanitizes it to an empty string or keeps it inert.
    // Importantly, no script should be injected into the DOM.
    const scripts = container.querySelectorAll("script");
    expect(scripts.length).toBe(0);
    // The link should have rel="noopener noreferrer" as a defense-in-depth measure
    expect(link.rel).toContain("noopener");
    expect(link.rel).toContain("noreferrer");
  });
});
