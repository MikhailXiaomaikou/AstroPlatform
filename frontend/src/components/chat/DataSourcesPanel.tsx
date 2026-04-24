import type { ConversationProvenance, ProvenanceDataset } from "../../hooks/useConversationProvenance";

const AUTHORITY_META: Record<string, { label: string; tone: "good" | "warn" | "registry" | "neutral"; title: string }> = {
  datacenter_ivoa_compliant: {
    label: "IVOA",
    tone: "good",
    title: "Data center IVOA-compliant provenance",
  },
  datacenter_non_standard_tag: {
    label: "PARAM",
    tone: "warn",
    title: "Data center metadata from non-standard PARAM tags",
  },
  datacenter_non_standard_info: {
    label: "INFO",
    tone: "warn",
    title: "Data center metadata from non-standard INFO tags",
  },
  project_registry: {
    label: "Registry",
    tone: "registry",
    title: "Project registry fallback provenance",
  },
};

function displayName(dataset: ProvenanceDataset): string {
  return dataset.service_name || dataset.service_key || dataset.publisher || dataset.creator || "Unknown source";
}

function adsUrl(bibcode: string): string {
  if (bibcode.toLowerCase().startsWith("arxiv:")) {
    return `https://arxiv.org/abs/${encodeURIComponent(bibcode.replace(/^arXiv:/i, ""))}`;
  }
  return `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(bibcode)}/abstract`;
}

function linkForIvoid(dataset: ProvenanceDataset): string | undefined {
  if (dataset.ivoid?.startsWith("http")) return dataset.ivoid;
  return dataset.credits_page_url || dataset.reference_url || dataset.source_urls?.[0];
}

function AuthorityCue({ authority, standard }: { authority?: string; standard?: string }) {
  const meta = authority ? AUTHORITY_META[authority] : undefined;
  const fallback = {
    label: authority || "Unknown",
    tone: "neutral" as const,
    title: authority ? `Source authority: ${authority}` : "Source authority unavailable",
  };
  const cue = meta || fallback;
  const label = standard ? `${cue.label} / ${standard}` : cue.label;
  const title = standard ? `${cue.title}; standard: ${standard}` : cue.title;
  return (
    <span
      className={`data-source-authority data-source-authority-${cue.tone}`}
      title={title}
      aria-label={`source authority: ${label}`}
    >
      <span className="data-source-authority-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

function DataSourceItem({ dataset }: { dataset: ProvenanceDataset }) {
  const ivoidHref = linkForIvoid(dataset);
  return (
    <article className="data-source-item">
      <div className="data-source-item-header">
        <strong>{displayName(dataset)}</strong>
        <AuthorityCue authority={dataset.source_authority} standard={dataset.standard} />
      </div>
      <dl className="data-source-fields">
        {dataset.archive_version && (
          <>
            <dt>Archive</dt>
            <dd>{dataset.archive_version}</dd>
          </>
        )}
        {dataset.ivoid && (
          <>
            <dt>IVOID</dt>
            <dd>
              {ivoidHref ? (
                <a href={ivoidHref} target="_blank" rel="noopener noreferrer">
                  {dataset.ivoid}
                </a>
              ) : (
                dataset.ivoid
              )}
            </dd>
          </>
        )}
        {dataset.article && (
          <>
            <dt>Bibcode</dt>
            <dd>
              <a href={adsUrl(dataset.article)} target="_blank" rel="noopener noreferrer">
                {dataset.article}
              </a>
            </dd>
          </>
        )}
        {(dataset.credits_page_url || dataset.reference_url) && (
          <>
            <dt>Credits</dt>
            <dd>
              <a href={dataset.credits_page_url || dataset.reference_url} target="_blank" rel="noopener noreferrer">
                Credits page
              </a>
            </dd>
          </>
        )}
      </dl>
    </article>
  );
}

export default function DataSourcesPanel({ summary }: { summary: ConversationProvenance }) {
  if (summary.datasets.length === 0 && summary.fieldBibcodeCount === 0 && summary.measurementReferenceCount === 0) {
    return null;
  }

  return (
    <details className="data-sources-panel">
      <summary>
        <span>Data Sources</span>
        {summary.datasets.length > 0 && (
          <span className="data-source-count">{summary.datasets.length}</span>
        )}
        {summary.fieldBibcodeCount > 0 && (
          <span className="field-bibcode-count">
            {summary.fieldBibcodeCount} per-value reference{summary.fieldBibcodeCount === 1 ? "" : "s"}
          </span>
        )}
        {summary.measurementReferenceCount > 0 && (
          <span className="field-bibcode-count">
            {summary.measurementReferenceCount} measurement table reference{summary.measurementReferenceCount === 1 ? "" : "s"}
          </span>
        )}
      </summary>
      <div className="data-sources-panel-body">
        {summary.datasets.map((dataset) => (
          <DataSourceItem key={dataset.ivoid || dataset.service_key || dataset.article || displayName(dataset)} dataset={dataset} />
        ))}
        {summary.fieldBibcodeCount > 0 && (
          <div className="field-bibcode-detail">
            <strong>{summary.fieldBibcodeCount}</strong> field-level bibcode{summary.fieldBibcodeCount === 1 ? "" : "s"} captured from{" "}
            {Object.keys(summary.fieldBibcodesByColumn).join(", ")}
          </div>
        )}
        {summary.measurementReferenceCount > 0 && (
          <div className="field-bibcode-detail">
            <strong>{summary.measurementReferenceCount}</strong> literature-table reference{summary.measurementReferenceCount === 1 ? "" : "s"} support typed measurement rows.
          </div>
        )}
      </div>
    </details>
  );
}
