import type { EvidenceReceiptV1 } from "../../api/client";
import { useI18n } from "../../i18n";

function text(value: unknown, fallback = "—"): string {
  const normalized = value == null ? "" : String(value).trim();
  return normalized || fallback;
}

function shortHash(value: unknown): string {
  const hash = text(value);
  return hash.length > 20 ? `${hash.slice(0, 16)}…` : hash;
}

function requestedSources(subject: Record<string, unknown>): string[] {
  if (!Array.isArray(subject.requested_sources)) return [];
  return subject.requested_sources.flatMap((source) => {
    if (!source || typeof source !== "object") return [];
    const record = source as Record<string, unknown>;
    return [`${text(record.kind)}:${text(record.identifier)}`];
  });
}

export function EvidenceReceiptCard({ receipt }: { receipt: EvidenceReceiptV1 }) {
  const { t } = useI18n();
  const kind = receipt.receipt_kind;
  const isUntrusted = kind === "untrusted_evidence";
  const isGap = kind === "capability_gap";
  const accent = isUntrusted ? "#b42318" : isGap ? "#9a6700" : "#197147";
  const background = isUntrusted ? "#fff4f2" : isGap ? "#fff8e6" : "#f2fbf6";
  const sources = requestedSources(receipt.subject);
  const toolAttempts = receipt.source_evidence.filter((item) => item.tool);
  const coverageSource = kind === "dataset_coverage"
    ? receipt.source_evidence.find((item) => item.source_locator)
    : undefined;
  const localizedBoundary = [
    "dataset_coverage",
    "capability_gap",
    "untrusted_evidence",
  ].includes(kind)
    ? t(`chat.evidence_receipt.boundary_${kind}`)
    : text(receipt.boundary_statement);
  const dependencyLabel = (dependency: string) => {
    const known: Record<string, string> = {
      "native early-dark-energy (EDE) model implementation": "ede_model",
      "exact Planck high-l and low-l TT/EE likelihoods": "planck_likelihoods",
      "DESI DR2 BAO data and covariance in the same run": "desi_dr2_covariance",
      "the requested supernova likelihood": "supernova_likelihood",
      "production sampler with convergence diagnostics": "production_sampler",
      "a backend-recorded current-turn tool result with registered provenance": "backend_rerun",
    };
    const key = known[dependency];
    return key ? t(`chat.evidence_receipt.dependency_${key}`) : dependency;
  };

  return (
    <article
      aria-label={t(`chat.evidence_receipt.title_${kind}`)}
      data-testid={`evidence-receipt-${kind}`}
      style={{
        border: `1px solid ${accent}`,
        borderLeftWidth: 4,
        borderRadius: 8,
        marginTop: 10,
        padding: "9px 11px",
        background,
        color: "var(--color-text-primary, #1f1f1f)",
        fontSize: "0.78rem",
        lineHeight: 1.45,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <strong>{t(`chat.evidence_receipt.title_${kind}`)}</strong>
        <span style={{ color: accent, fontWeight: 700 }}>
          {t(`chat.evidence_receipt.status_${receipt.source_status}`)}
        </span>
      </div>

      {kind === "dataset_coverage" && (
        <div style={{ marginTop: 6 }}>
          <div>
            <strong>{text(receipt.subject.display_name)}</strong>{" "}
            · {text(receipt.subject.dataset_version)}
          </div>
          <div>
            {t("chat.evidence_receipt.requested_redshift")} {text(receipt.facts.requested_redshift)}
            {" · "}{t("chat.evidence_receipt.registered_range")} {text(receipt.facts.z_min)}–{text(receipt.facts.z_max)}
          </div>
          <div>{t("chat.evidence_receipt.registry_version")} {text(receipt.facts.registry_version)}</div>
          {coverageSource && (
            <details style={{ marginTop: 5 }}>
              <summary style={{ cursor: "pointer", fontWeight: 650 }}>
                {t("chat.evidence_receipt.source_details")}
              </summary>
              <div>{text(coverageSource.source_locator)}</div>
              <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "0.7rem" }}>
                {t("chat.evidence_receipt.data_product_hash")}: {shortHash(coverageSource.data_product_sha256)}
              </div>
            </details>
          )}
        </div>
      )}

      {kind === "capability_gap" && (
        <div style={{ marginTop: 6 }}>
          {sources.length > 0 && (
            <div><strong>{t("chat.evidence_receipt.requested_source")}:</strong> {sources.join(", ")}</div>
          )}
          <div>
            {t("chat.evidence_receipt.tool_attempts")}: {text(receipt.facts.tool_attempt_count, "0")}
          </div>
          {toolAttempts.length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {toolAttempts.map((attempt, index) => (
                <li key={`${text(attempt.tool)}-${index}`}>
                  {text(attempt.tool)} — {text(attempt.status)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {kind === "untrusted_evidence" && (
        <div style={{ marginTop: 6, color: accent, fontWeight: 650 }}>
          {t("chat.evidence_receipt.untrusted_reason")}
        </div>
      )}

      {receipt.missing_dependencies.length > 0 && (
        <details style={{ marginTop: 7 }}>
          <summary style={{ cursor: "pointer", fontWeight: 650 }}>
            {isUntrusted
              ? t("chat.evidence_receipt.legal_rerun")
              : t("chat.validation.missing_dependencies")}
          </summary>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {receipt.missing_dependencies.map((dependency, index) => (
              <li key={`${dependency}-${index}`}>{dependencyLabel(dependency)}</li>
            ))}
          </ul>
        </details>
      )}

      <div style={{ marginTop: 7, paddingTop: 6, borderTop: "1px solid rgba(0,0,0,0.12)" }}>
        <strong>{t("chat.scalar_receipt.boundary")}:</strong>{" "}
        {localizedBoundary}
      </div>
      <div style={{ marginTop: 3, color: "var(--color-text-tertiary, #666)", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "0.68rem" }}>
        {t("chat.scalar_receipt.receipt_hash")}: {shortHash(receipt.receipt_sha256)}
      </div>
    </article>
  );
}

export function EvidenceReceiptCards({ receipts }: { receipts?: EvidenceReceiptV1[] }) {
  if (!Array.isArray(receipts) || receipts.length === 0) return null;
  return (
    <section aria-label="Evidence receipts">
      {receipts.map((receipt, index) => (
        <EvidenceReceiptCard
          key={`${receipt.receipt_kind}-${receipt.receipt_sha256 || index}`}
          receipt={receipt}
        />
      ))}
    </section>
  );
}
