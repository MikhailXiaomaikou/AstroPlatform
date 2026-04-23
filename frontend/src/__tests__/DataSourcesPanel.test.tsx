import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AckButton from "../components/chat/AckButton";
import DataSourcesPanel from "../components/chat/DataSourcesPanel";
import { aggregateConversationProvenance } from "../hooks/useConversationProvenance";

const provenancePayload = {
  provenance: {
    datasets: [
      {
        service_key: "gaia",
        service_name: "Gaia DR3",
        archive_version: "Gaia DR3",
        ivoid: "ivo://esavo/gaia/dr3",
        article: "2023A&A...674A...1G",
        publisher: "European Space Agency / Gaia Archive",
        credits_page_url: "https://www.cosmos.esa.int/web/gaia-users/archive/credits",
        source_authority: "datacenter_non_standard_tag",
      },
      {
        service_key: "simbad",
        service_name: "SIMBAD",
        archive_version: "SIMBAD live service",
        ivoid: "ivo://simbad.u-strasbg/simbad",
        article: "2000A&AS..143....9W",
        publisher: "CDS",
        credits_page_url: "https://simbad.cds.unistra.fr/guide/ch14.htx",
        source_authority: "project_registry",
      },
    ],
    field_bibcodes: {
      columns: {
        plx_bibcode: ["2020yCat.1350....0G"],
        rv_bibcode: ["2008yCat....102025B"],
      },
    },
  },
};

describe("DataSourcesPanel", () => {
  it("shows archive versions, bibcodes, authority cues, and field-level counts", () => {
    const summary = aggregateConversationProvenance(provenancePayload);

    render(<DataSourcesPanel summary={summary} />);

    expect(screen.getByText("Data Sources")).toBeInTheDocument();
    expect(screen.getAllByText("Gaia DR3").length).toBeGreaterThan(0);
    expect(screen.getByText("SIMBAD")).toBeInTheDocument();
    expect(screen.getByText("2023A&A...674A...1G")).toBeInTheDocument();
    expect(screen.getByText("2 per-value references")).toBeInTheDocument();
    expect(screen.getByLabelText("source authority: PARAM")).toBeInTheDocument();
    expect(screen.getByLabelText("source authority: Registry")).toBeInTheDocument();
  });
});

describe("AckButton", () => {
  it("copies acknowledgement text and reports completion", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const onCopied = vi.fn();
    const summary = aggregateConversationProvenance(provenancePayload);

    render(<AckButton summary={summary} onCopied={onCopied} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy Acknowledgement" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Gaia Archive")));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("2000A&AS..143....9W"));
    expect(onCopied).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });
});
