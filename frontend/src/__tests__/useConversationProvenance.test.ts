import { describe, expect, it } from "vitest";
import {
  aggregateConversationProvenance,
  buildAcknowledgementText,
} from "../hooks/useConversationProvenance";

const vizierDataset = {
  service_key: "vizier",
  service_name: "VizieR",
  archive_version: "GCVS 2024 mirror",
  ivoid: "ivo://CDS.VizieR/B/gcvs",
  article: "2017ARep...61...80S",
  publisher: "CDS",
  credits_page_url: "https://vizier.cds.unistra.fr/vizier/cite.htx",
  source_authority: "datacenter_ivoa_compliant",
};

describe("aggregateConversationProvenance", () => {
  it("dedupes datasets and counts unique field-level bibcodes", () => {
    const summary = aggregateConversationProvenance([
      {
        role: "assistant",
        actions: [
          {
            action: "run_adql",
            tool_result: {
              provenance: {
                datasets: [vizierDataset, { ...vizierDataset, publisher: "CDS duplicate" }],
                field_bibcodes: {
                  columns: {
                    plx_bibcode: ["2020yCat.1350....0G", "2020yCat.1350....0G"],
                    rv_bibcode: ["2000A&AS..143....9W"],
                  },
                },
              },
            },
          },
        ],
      },
    ]);

    expect(summary.datasets).toHaveLength(1);
    expect(summary.datasets[0]?.service_name).toBe("VizieR");
    expect(summary.fieldBibcodeCount).toBe(2);
    expect(summary.fieldBibcodesByColumn.plx_bibcode).toHaveLength(2);
    expect(summary.fullyCovered).toBe(true);
  });

  it("builds the dual-layer acknowledgement template from dataset provenance", () => {
    const text = buildAcknowledgementText([vizierDataset]);

    expect(text).toContain("CDS, 2017ARep...61...80S");
    expect(text).toContain("https://vizier.cds.unistra.fr/vizier/cite.htx");
    expect(text).toContain("Individual measurements are cited inline using field-level bibcodes");
  });
});
