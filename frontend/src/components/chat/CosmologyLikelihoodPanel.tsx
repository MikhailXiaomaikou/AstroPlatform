type Citation = {
  label?: string;
  year?: number;
  arxiv?: string | null;
  doi?: string | null;
};

type DatasetEntry = {
  key?: string;
  display_name?: string;
  version?: string;
  probe?: string;
  status?: string;
  source_url?: string;
  covariance?: { kind?: string; provided?: boolean; description?: string };
  citations?: Citation[];
  execution_mode?: string;
  compressed_likelihood?: { parameters?: string[]; source_locator?: string };
  data_products?: {
    product_type?: string;
    role?: string;
    url?: string;
    format?: string;
    description?: string;
    columns?: string[];
    rows?: number | null;
  }[];
};

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function shortHash(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  return text ? text.slice(0, 12) : "—";
}

function statusTone(status: string | undefined): { label: string; color: string; bg: string; border: string } {
  const key = String(status || "unknown").toLowerCase();
  if (key === "ready") {
    return { label: "ready", color: "#1b7f42", bg: "rgba(34, 197, 94, 0.10)", border: "#2fbf71" };
  }
  if (key === "external_likelihood" || key === "external_cobaya" || key === "external_cosmosis") {
    return { label: "external likelihood", color: "#8a5b00", bg: "rgba(255, 183, 0, 0.13)", border: "#d99a00" };
  }
  if (key === "compressed_gaussian") {
    return { label: "compressed gaussian", color: "#155e75", bg: "rgba(14, 165, 233, 0.10)", border: "#0ea5e9" };
  }
  return { label: key.replace(/_/g, " "), color: "#7b2d26", bg: "rgba(123, 45, 38, 0.10)", border: "#b66a61" };
}

function Badge({ children, status }: { children: string; status?: string }) {
  const tone = statusTone(status);
  return (
    <span
      style={{
        color: tone.color,
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 4,
        padding: "1px 6px",
        fontSize: "0.7rem",
        fontWeight: 600,
      }}
    >
      {children}
    </span>
  );
}

function DatasetList({ datasets }: { datasets: DatasetEntry[] }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {datasets.slice(0, 10).map((entry) => {
        const citation = asArray<Citation>(entry.citations)[0];
        const dataProducts = asArray<NonNullable<DatasetEntry["data_products"]>[number]>(entry.data_products);
        const productRoles = dataProducts
          .map((product) => product.role || product.product_type)
          .filter(Boolean)
          .slice(0, 3)
          .join(", ");
        return (
          <div
            key={entry.key || entry.display_name}
            style={{
              border: "1px solid var(--color-border)",
              borderRadius: 6,
              padding: "7px 8px",
              background: "rgba(255,255,255,0.45)",
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <strong style={{ color: "var(--color-text-primary)" }}>{entry.display_name || entry.key}</strong>
              <Badge status={entry.status}>{statusTone(entry.status).label}</Badge>
              {entry.execution_mode ? <Badge status={entry.execution_mode}>{entry.execution_mode.replace(/_/g, " ")}</Badge> : null}
              {entry.probe ? <span style={{ color: "var(--color-text-tertiary)" }}>{entry.probe}</span> : null}
            </div>
            <div style={{ marginTop: 4, color: "var(--color-text-secondary)" }}>
              {entry.version || "version not reported"} · covariance: {entry.covariance?.kind || "not reported"}
              {entry.covariance?.provided === false ? " (recipe/manual)" : ""}
            </div>
            {entry.compressed_likelihood ? (
              <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem", marginTop: 2 }}>
                executable compressed params: {asArray<string>(entry.compressed_likelihood.parameters).join(", ")}
              </div>
            ) : null}
            {dataProducts.length ? (
              <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem", marginTop: 2 }}>
                machine-readable products: {dataProducts.length}
                {productRoles ? ` · ${productRoles}` : ""}
                {dataProducts[0]?.url ? (
                  <>
                    {" · "}
                    <a href={dataProducts[0].url} target="_blank" rel="noreferrer">
                      source
                    </a>
                  </>
                ) : null}
              </div>
            ) : null}
            {citation ? (
              <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem", marginTop: 2 }}>
                {citation.label}{citation.year ? ` (${citation.year})` : ""}{citation.arxiv ? ` · arXiv:${citation.arxiv}` : ""}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export default function CosmologyLikelihoodPanel({ result }: { result: Record<string, unknown> }) {
  const datasets = asArray<DatasetEntry>(result.datasets);
  const matrix = asArray<Record<string, unknown>>(result.matrix);
  const warnings = asArray<string>(result.warnings);
  const hasConfig = Boolean(result.config_hash || result.cobaya || result.cosmosis);
  const isMatrix = matrix.length > 0;
  const title = isMatrix
    ? "Cosmology Robustness Matrix"
    : hasConfig
      ? "Cosmology Likelihood Config"
      : "Cosmology Dataset Registry";

  return (
    <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <strong style={{ color: "var(--color-text-primary)" }}>{title}</strong>
        {result.model ? <span>{String(result.model)}</span> : null}
        {hasConfig ? <Badge status="external_likelihood">config only</Badge> : null}
        {result.analysis_status === "COMPRESSED_ROBUSTNESS_READY" ? <Badge status="ready">compressed results</Badge> : null}
        {result.dataset_count != null ? <span>{String(result.dataset_count)} datasets</span> : null}
        {result.matrix_size != null ? <span>{String(result.matrix_size)} runs</span> : null}
      </div>

      {hasConfig && (
        <div
          style={{
            padding: "6px 8px",
            borderLeft: "3px solid #d99a00",
            background: "rgba(255, 183, 0, 0.10)",
            color: "var(--color-text-primary)",
            marginBottom: 8,
          }}
        >
          Config hash {shortHash(result.config_hash)}. Posterior, tension, AIC/BIC, and robustness
          claims remain non-citeable until a chain returns publication-ready diagnostics.
        </div>
      )}

      {warnings.map((warning) => (
        <div key={warning} style={{ color: "#8a5b00", marginBottom: 4 }}>⚠ {warning}</div>
      ))}

      {isMatrix ? (
        <div style={{ display: "grid", gap: 6 }}>
          {matrix.slice(0, 12).map((row, index) => (
            <div key={`${row.label || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
              <strong style={{ color: "var(--color-text-primary)" }}>{String(row.label || `Run ${index + 1}`)}</strong>
              <div style={{ color: "var(--color-text-tertiary)" }}>
                {asArray<string>(row.dataset_keys).join(" + ")} · config {shortHash(row.config_hash)}
              </div>
              {row.result && typeof row.result === "object" ? (
                <div style={{ marginTop: 3, color: row.publication_ready ? "#1b7f42" : "#8a5b00" }}>
                  {row.publication_ready ? "compressed posterior ready" : "not runnable as posterior"}
                  {" · "}
                  used {asArray<DatasetEntry>((row.result as Record<string, unknown>).datasets_used).length} compressed dataset(s)
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : datasets.length > 0 ? (
        <DatasetList datasets={datasets} />
      ) : null}
    </div>
  );
}
