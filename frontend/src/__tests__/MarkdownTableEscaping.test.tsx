import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import MarkdownText from "../components/chat/MarkdownText";

/**
 * The backend escapes a pipe inside a table cell as a backslash-pipe
 * (research_program._md_table_cell), so a dataset key or an error string
 * containing one cannot break the row. Splitting the row on every pipe undid
 * that escaping and shifted every later cell by one column.
 */
describe("markdown table cells with escaped pipes", () => {
  it("keeps an escaped pipe inside its own cell", () => {
    const table = [
      "| label | detail |",
      "| --- | --- |",
      "| desi\\|dr2 | failed: bad\\|input |",
    ].join("\n");
    render(<MarkdownText content={table} />);
    expect(screen.getByText("desi|dr2")).toBeTruthy();
    expect(screen.getByText("failed: bad|input")).toBeTruthy();
  });

  it("still splits ordinary rows on their real column separators", () => {
    const table = ["| a | b |", "| --- | --- |", "| one | two |"].join("\n");
    const { container } = render(<MarkdownText content={table} />);
    const cells = container.querySelectorAll("tbody td");
    expect(cells.length).toBe(2);
    expect(cells[0].textContent).toBe("one");
    expect(cells[1].textContent).toBe("two");
  });
});
