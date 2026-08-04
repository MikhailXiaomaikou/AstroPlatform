import { useI18n } from "../../i18n";

type Quantity = {
  id?: string;
  label?: string;
  value?: number;
  standard_uncertainty?: number;
  unit?: string;
  source_ref?: string;
  source_locator?: string;
};

type SourceEvidence = {
  id?: string;
  kind?: string;
  identifier?: string;
  locator?: string;
  status?: string;
  extraction_method?: string;
  sha256?: string;
  cache_hit?: boolean;
  final_url?: string;
  match?: { reason?: string };
};

function stringValue(value: unknown, fallback = "—"): string {
  const text = value == null ? "" : String(value).trim();
  return text || fallback;
}

function shortHash(value: unknown): string {
  const hash = stringValue(value);
  return hash.length > 24 ? `${hash.slice(0, 20)}…` : hash;
}

export default function ScalarVerificationReceiptCard({
  result,
}: {
  result: Record<string, unknown>;
}) {
  const { t } = useI18n();
  const disposition = stringValue(result.response_disposition, "abstention");
  const sourceStatus = stringValue(result.source_status, "unavailable");
  const calculation = result.result && typeof result.result === "object"
    ? result.result as Record<string, unknown>
    : null;
  const inputs = Array.isArray(result.inputs) ? result.inputs as Quantity[] : [];
  const evidence = Array.isArray(result.source_evidence)
    ? result.source_evidence as SourceEvidence[]
    : [];
  const assumptions = Array.isArray(result.assumptions)
    ? result.assumptions.filter((item): item is string => typeof item === "string")
    : [];
  const uncertaintyModel = result.uncertainty_model && typeof result.uncertainty_model === "object"
    ? result.uncertainty_model as Record<string, unknown>
    : {};
  const conflict = sourceStatus === "conflict";
  const full = disposition === "full" && sourceStatus === "verified_exact";
  const abstention = disposition === "abstention";
  const accent = conflict || abstention ? "#b00020" : full ? "#1b5e20" : "#8a6a00";
  const background = conflict || abstention ? "#fdf2f4" : full ? "#f1f8f1" : "#fff8e6";
  const sourceLabel = sourceStatus === "verified_exact"
    ? t("chat.scalar_receipt.source_verified")
    : sourceStatus === "conflict"
      ? t("chat.scalar_receipt.source_conflict")
      : t("chat.scalar_receipt.source_unverified");

  return (
    <article
      aria-label={t("chat.scalar_receipt.title")}
      data-testid="scalar-verification-receipt"
      style={{
        border: `1px solid ${accent}`,
        borderLeftWidth: 4,
        borderRadius: 8,
        padding: "10px 12px",
        background,
        color: "var(--color-text-primary, #1f1f1f)",
        fontSize: "0.78rem",
        lineHeight: 1.5,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <strong>{t("chat.scalar_receipt.title")}</strong>
        <span style={{ color: accent, fontWeight: 700 }}>
          {t(`chat.scalar_receipt.disposition_${disposition}`)}
        </span>
      </div>

      <div style={{ marginTop: 4 }}>
        <strong>{t("chat.validation.task_kind")}:</strong>{" "}
        {stringValue(result.task_kind, "deterministic_source_check")}
      </div>

      <div style={{ marginTop: 4, color: accent, fontWeight: 650 }}>{sourceLabel}</div>

      {calculation ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: "1rem", fontWeight: 700 }}>
            {stringValue(calculation.rounded_display)}
          </div>
          <div>
            <strong>{t("chat.scalar_receipt.formula")}:</strong>{" "}
            <code>{stringValue(result.formula)}</code>
          </div>
          <div>
            <strong>{t("chat.scalar_receipt.uncertainty_model")}:</strong>{" "}
            {stringValue(uncertaintyModel.kind)}
          </div>
          {calculation.standardized_difference_display != null && (
            <div>
              <strong>{t("chat.scalar_receipt.standardized_difference")}:</strong>{" "}
              {stringValue(calculation.standardized_difference_display)}
            </div>
          )}
          {calculation.independent_standard_uncertainty != null && (
            <div>
              <strong>{t("chat.scalar_receipt.independent_comparison")}:</strong>{" "}
              σ={stringValue(calculation.independent_standard_uncertainty)}, Δ={stringValue(calculation.relative_uncertainty_change_vs_independent)}
            </div>
          )}
        </div>
      ) : (
        <div style={{ marginTop: 8, color: accent }}>
          {stringValue(result.error, t("chat.scalar_receipt.no_result"))}
        </div>
      )}

      {inputs.length > 0 && (
        <details style={{ marginTop: 8 }} open>
          <summary style={{ cursor: "pointer", fontWeight: 650 }}>
            {t("chat.scalar_receipt.inputs")}
          </summary>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(80px, auto) 1fr", gap: "2px 10px", marginTop: 4 }}>
            {inputs.map((input, index) => (
              <div key={`${input.id || input.label || "input"}-${index}`} style={{ display: "contents" }}>
                <span>{stringValue(input.label || input.id)}</span>
                <span>
                  {stringValue(input.value)} ± {stringValue(input.standard_uncertainty)} {stringValue(input.unit, "")}
                  {input.source_locator ? ` · ${input.source_locator}` : ""}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}

      {evidence.length > 0 && (
        <details style={{ marginTop: 8 }} open>
          <summary style={{ cursor: "pointer", fontWeight: 650 }}>
            {t("chat.scalar_receipt.sources")}
          </summary>
          {evidence.map((source, index) => (
            <div
              key={`${source.id || source.identifier || "source"}-${index}`}
              style={{ marginTop: 5, paddingTop: 5, borderTop: index ? "1px solid rgba(0,0,0,0.08)" : undefined }}
            >
              <div>
                <strong>{stringValue(source.kind)}:{stringValue(source.identifier || source.id)}</strong>{" "}
                <span style={{ color: source.status === "conflict" ? "#b00020" : undefined }}>
                  ({stringValue(source.status)})
                </span>
              </div>
              <div>{stringValue(source.locator)} · {stringValue(source.extraction_method)}</div>
              <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "0.7rem" }}>
                SHA-256 {shortHash(source.sha256)}{source.cache_hit ? ` · ${t("chat.scalar_receipt.cache_hit")}` : ""}
              </div>
            </div>
          ))}
        </details>
      )}

      {assumptions.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <strong>{t("chat.scalar_receipt.assumptions")}:</strong> {assumptions.join(" ")}
        </div>
      )}
      <div style={{ marginTop: 8, paddingTop: 7, borderTop: "1px solid rgba(0,0,0,0.12)" }}>
        <strong>{t("chat.scalar_receipt.boundary")}:</strong>{" "}
        {stringValue(result.boundary_statement)}
      </div>
      <div style={{ marginTop: 4, color: "var(--color-text-tertiary, #666)", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "0.68rem" }}>
        {t("chat.scalar_receipt.receipt_hash")}: {shortHash(result.receipt_sha256)}
      </div>
    </article>
  );
}
