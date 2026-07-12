import PanelEmptyState from "./PanelEmptyState";

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
  compressed_likelihood?: {
    parameters?: string[];
    source_locator?: string;
    statistical_role?: string;
    source_prior?: string | null;
  };
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function fmtNumber(value: unknown, digits = 3): string {
  // Number(null) === 0, so a JSON null (e.g. rhat: not computed on the
  // in-process runner) must be caught BEFORE coercion — otherwise the UI
  // renders a fabricated "0.000" for a diagnostic that was never computed.
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(digits);
}

function matrixRunLabel(row: Record<string, unknown>): string {
  if (row.publication_ready) return "publication-ready numerical result";
  const level = String(row.execution_level || "not_available");
  if (level === "partial_dataset_run") return "partial posterior; some datasets not included";
  if (level === "executed_not_ready") return "numerical run withheld by scientific gate";
  if (level === "context_only") return "literature context only; no likelihood run";
  if (level === "config_only") return "configuration only, no posterior run yet";
  return level.replace(/_/g, " ");
}

function gaussianRecordLabel(entry: DatasetEntry): string {
  const role = entry.compressed_likelihood?.statistical_role;
  if (role === "published_posterior_summary") return "literature posterior context";
  if (role === "proposal_only") return "proposal-only context";
  if (role === "external_prior") {
    return entry.execution_mode === "compressed_gaussian"
      ? "executable external prior"
      : "external prior (configuration only)";
  }
  if (role === "likelihood_approximation") {
    return entry.execution_mode === "compressed_gaussian"
      ? "executable likelihood approximation"
      : "likelihood approximation (configuration only)";
  }
  return "unclassified Gaussian record";
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
                {gaussianRecordLabel(entry)}: {asArray<string>(entry.compressed_likelihood.parameters).join(", ")}
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
  const parse = asRecord(result.parse);
  const product = asRecord(result.product);
  const isDataProduct = Boolean(
    result.dataset_key && (Object.keys(parse).length > 0 || Object.keys(product).length > 0)
  );
  const title = isDataProduct
    ? "Cosmology Data Product"
    : isMatrix
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
        {result.analysis_status === "ROBUSTNESS_MATRIX_DIAGNOSTIC" ? (
          <Badge status="external_likelihood">diagnostic matrix</Badge>
        ) : null}
        {isDataProduct && result.hash_verified === true ? <Badge status="ready">hash verified</Badge> : null}
        {isDataProduct && result.hash_verified === false ? <Badge status="external_likelihood">hash unpinned</Badge> : null}
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

      {isDataProduct ? (
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ color: "var(--color-text-primary)" }}>
            <strong>{String(result.dataset_display_name || result.dataset_key)}</strong>
            {result.dataset_version ? <span style={{ color: "var(--color-text-tertiary)" }}> · {String(result.dataset_version)}</span> : null}
          </div>
          {Object.keys(product).length > 0 ? (
            <div style={{ color: "var(--color-text-secondary)", fontSize: "0.74rem" }}>
              {product.role ? <span>role: <code>{String(product.role)}</code> · </span> : null}
              {product.product_type ? <span>type: <code>{String(product.product_type)}</code> · </span> : null}
              {product.format ? <span>format: {String(product.format)}</span> : null}
              {product.url ? (
                <>
                  {" · "}
                  <a href={String(product.url)} target="_blank" rel="noreferrer">source</a>
                </>
              ) : null}
            </div>
          ) : null}
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>
            sha256 {shortHash(result.sha256)}
            {result.hash_verified === true ? " · verified against registry" : ""}
            {result.hash_verified === false && product.sha256 ? " · mismatch with registry sha256" : ""}
          </div>
          {parse.kind === "matrix" || Array.isArray(parse.covariance_shape) ? (() => {
            const shape = asArray<number>(parse.shape ?? parse.covariance_shape);
            return (
              <div style={{ color: "var(--color-text-secondary)", fontSize: "0.74rem" }}>
                shape {shape.length > 0 ? shape.join("×") : "—"}
                {" · "}
                symmetric={String(parse.symmetric ?? parse.covariance_symmetric ?? "—")}
                {" · "}
                positive diag={String(parse.positive_diagonal ?? "—")}
                {parse.finite != null ? <> · finite={String(parse.finite)}</> : null}
              </div>
            );
          })() : null}
          {parse.kind === "table" ? (
            <div style={{ color: "var(--color-text-secondary)", fontSize: "0.74rem" }}>
              {parse.row_count != null ? `${String(parse.row_count)} rows` : ""}
              {Array.isArray(parse.declared_columns) && (parse.declared_columns as string[]).length > 0
                ? ` · columns: ${(parse.declared_columns as string[]).join(", ")}`
                : ""}
            </div>
          ) : null}
        </div>
      ) : isMatrix ? (
        <div style={{ display: "grid", gap: 6 }}>
          {matrix.slice(0, 12).map((row, index) => (
            <div key={`${row.label || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
              <strong style={{ color: "var(--color-text-primary)" }}>{String(row.label || `Run ${index + 1}`)}</strong>
              <div style={{ color: "var(--color-text-tertiary)" }}>
                {asArray<string>(row.dataset_keys).join(" + ")} · config {shortHash(row.config_hash)}
              </div>
              {row.result && typeof row.result === "object" ? (
                <div style={{ marginTop: 3, color: row.publication_ready ? "#1b7f42" : "#8a5b00" }}>
                  {matrixRunLabel(row)}
                  {" · "}
                  used {asArray<DatasetEntry>((row.result as Record<string, unknown>).datasets_used).length} dataset(s)
                </div>
              ) : null}
              {row.result && typeof row.result === "object" ? (() => {
                const resultObj = asRecord(row.result);
                const diagnostics = asRecord(resultObj.chain_diagnostics);
                const parameters = asRecord(resultObj.parameters);
                const h0 = asRecord(parameters.H0);
                const notRun = asArray<DatasetEntry>(resultObj.datasets_not_run);
                return (
                  <>
                    {Object.keys(diagnostics).length || Object.keys(h0).length ? (
                      <div style={{ color: "var(--color-text-secondary)", fontSize: "0.72rem", marginTop: 2 }}>
                        H0 median {fmtNumber(h0.median)}
                        {" · "}ESS {fmtNumber(diagnostics.proposal_ess ?? diagnostics.ess_bulk, 1)}
                        {" · "}Rhat {fmtNumber(diagnostics.rhat, 3)}
                      </div>
                    ) : null}
                    {notRun.length ? (
                      <div style={{ color: "#8a5b00", fontSize: "0.72rem", marginTop: 2 }}>
                        not numerically included: {notRun.map((entry) => entry.key || entry.display_name || "dataset").join(", ")}
                      </div>
                    ) : null}
                  </>
                );
              })() : null}
            </div>
          ))}
        </div>
      ) : datasets.length > 0 ? (
        <DatasetList datasets={datasets} />
      ) : (
        // Final fallback: no data product, no matrix, no datasets — show a
        // status-aware empty state instead of a chrome-only card.
        <PanelEmptyState
          status={String(result.__tool_status__ || result.analysis_status || "UNKNOWN")}
          message={
            typeof result.__message_to_model__ === "string"
              ? result.__message_to_model__
              : typeof result.error === "string"
                ? result.error
                : undefined
          }
        />
      )}
    </div>
  );
}
