import { useMemo } from "react";

type UnknownRecord = Record<string, unknown>;

export interface ProvenanceDataset {
  service_key?: string;
  service_name?: string;
  ivoid?: string;
  article?: string;
  archive_version?: string;
  source_authority?: string;
  publisher?: string;
  creator?: string;
  reference_url?: string;
  credits_page_url?: string;
  acknowledgement_template?: string;
  source_urls?: string[];
}

export interface ConversationProvenance {
  datasets: ProvenanceDataset[];
  fieldBibcodesByColumn: Record<string, string[]>;
  field_bibcodes: Record<string, string[]>;
  fieldBibcodeCount: number;
  fullyCovered: boolean;
  fully_covered: boolean;
  acknowledgementText: string;
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asString(item))
    .filter((item): item is string => Boolean(item));
}

function normalizeDataset(value: unknown): ProvenanceDataset | null {
  if (!isRecord(value)) return null;
  const sourceUrls = asStringArray(value.source_urls);
  const dataset: ProvenanceDataset = {
    service_key: asString(value.service_key),
    service_name: asString(value.service_name),
    ivoid: asString(value.ivoid),
    article: asString(value.article),
    archive_version: asString(value.archive_version),
    source_authority: asString(value.source_authority),
    publisher: asString(value.publisher),
    creator: asString(value.creator),
    reference_url: asString(value.reference_url),
    credits_page_url: asString(value.credits_page_url),
    acknowledgement_template: asString(value.acknowledgement_template),
    source_urls: sourceUrls.length > 0 ? sourceUrls : undefined,
  };
  if (!dataset.service_key && !dataset.service_name && !dataset.ivoid && !dataset.article) {
    return null;
  }
  return dataset;
}

function datasetKey(dataset: ProvenanceDataset): string {
  return (
    dataset.ivoid
    || dataset.service_key
    || dataset.article
    || dataset.service_name
    || JSON.stringify(dataset)
  ).toLowerCase();
}

function mergeDataset(base: ProvenanceDataset, next: ProvenanceDataset): ProvenanceDataset {
  return {
    ...base,
    ...Object.fromEntries(
      Object.entries(next).filter(([, value]) => {
        if (Array.isArray(value)) return value.length > 0;
        return value !== undefined && value !== "";
      }),
    ),
    source_urls: Array.from(new Set([...(base.source_urls || []), ...(next.source_urls || [])])),
  };
}

function collectToolResults(source: unknown, out: UnknownRecord[] = []): UnknownRecord[] {
  if (Array.isArray(source)) {
    for (const item of source) collectToolResults(item, out);
    return out;
  }
  if (!isRecord(source)) return out;

  if (isRecord(source.tool_result)) {
    out.push(source.tool_result);
  } else if (isRecord(source.provenance) || Array.isArray(source.datasets) || isRecord(source.field_bibcodes)) {
    out.push(source);
  }

  if (Array.isArray(source.actions)) {
    collectToolResults(source.actions, out);
  }
  if (Array.isArray(source.messages)) {
    collectToolResults(source.messages, out);
  }
  return out;
}

function extractDatasets(toolResult: UnknownRecord): ProvenanceDataset[] {
  const provenance = isRecord(toolResult.provenance) ? toolResult.provenance : {};
  const rawDatasets = Array.isArray(provenance.datasets)
    ? provenance.datasets
    : Array.isArray(toolResult.datasets)
      ? toolResult.datasets
      : [];
  return rawDatasets
    .map((dataset) => normalizeDataset(dataset))
    .filter((dataset): dataset is ProvenanceDataset => dataset !== null);
}

function extractFieldBibcodes(toolResult: UnknownRecord): Record<string, string[]> {
  const provenance = isRecord(toolResult.provenance) ? toolResult.provenance : {};
  const fieldBibcodes = isRecord(provenance.field_bibcodes)
    ? provenance.field_bibcodes
    : isRecord(toolResult.field_bibcodes)
      ? toolResult.field_bibcodes
      : null;
  if (!fieldBibcodes || !isRecord(fieldBibcodes.columns)) return {};

  const result: Record<string, string[]> = {};
  for (const [column, rawValues] of Object.entries(fieldBibcodes.columns)) {
    const values = Array.isArray(rawValues)
      ? rawValues
          .map((value) => asString(value))
          .filter((value): value is string => Boolean(value))
      : [];
    if (values.length > 0) {
      result[column] = values;
    }
  }
  return result;
}

export function buildAcknowledgementText(datasets: ProvenanceDataset[]): string {
  const lines = ["This research has made use of the following resources:", ""];
  if (datasets.length === 0) {
    lines.push("- No authoritative data-center provenance was captured in this conversation.");
  } else {
    for (const dataset of datasets) {
      const owner = dataset.publisher || dataset.creator || dataset.service_name || dataset.service_key || "Unknown data service";
      const article = dataset.article ? `, ${dataset.article}` : "";
      const url = dataset.credits_page_url || dataset.reference_url || dataset.source_urls?.[0];
      lines.push(`- ${owner}${article}${url ? ` (${url})` : ""}`);
    }
  }
  lines.push(
    "",
    "Individual measurements are cited inline using field-level bibcodes where available, following the convention of each data center.",
    "",
    "Please consult each data center's credits page for final acknowledgement wording before submission.",
  );
  return lines.join("\n");
}

export function aggregateConversationProvenance(source: unknown): ConversationProvenance {
  const datasetsByKey = new Map<string, ProvenanceDataset>();
  const fieldBibcodesByColumn: Record<string, string[]> = {};
  const uniqueFieldBibcodes = new Set<string>();

  for (const toolResult of collectToolResults(source)) {
    for (const dataset of extractDatasets(toolResult)) {
      const key = datasetKey(dataset);
      const current = datasetsByKey.get(key);
      datasetsByKey.set(key, current ? mergeDataset(current, dataset) : dataset);
    }

    for (const [column, values] of Object.entries(extractFieldBibcodes(toolResult))) {
      const existing = fieldBibcodesByColumn[column] || [];
      fieldBibcodesByColumn[column] = [...existing, ...values];
      for (const value of values) uniqueFieldBibcodes.add(value);
    }
  }

  const datasets = Array.from(datasetsByKey.values());
  const fullyCovered = datasets.length > 0 && datasets.every((dataset) =>
    Boolean(dataset.archive_version && (dataset.article || dataset.credits_page_url || dataset.reference_url)),
  );

  return {
    datasets,
    fieldBibcodesByColumn,
    field_bibcodes: fieldBibcodesByColumn,
    fieldBibcodeCount: uniqueFieldBibcodes.size,
    fullyCovered,
    fully_covered: fullyCovered,
    acknowledgementText: buildAcknowledgementText(datasets),
  };
}

export function useConversationProvenance(source: unknown): ConversationProvenance {
  return useMemo(() => aggregateConversationProvenance(source), [source]);
}
