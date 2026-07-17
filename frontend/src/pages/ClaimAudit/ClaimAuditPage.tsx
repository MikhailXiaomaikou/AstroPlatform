import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  cancelClaimAudit,
  createClaimAudit,
  deleteClaimAudit,
  downloadEvidencePack,
  getClaimAudit,
  getRuntimeConfig,
  listClaimAudits,
  retryClaimAudit,
  verifyEvidencePack,
  verifyEvidencePackFile,
  type ClaimAuditCreatePayload,
  type ClaimAuditSummary,
  type ClaimAuditVerdict,
  type EvidencePackVerification,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import "./ClaimAuditPage.css";

const TERMINAL_STATES = new Set([
  "COMPLETED",
  "FAILED_RETRYABLE",
  "FAILED_FINAL",
  "CANCELLED",
]);

function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "response" in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function splitValues(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map((part) => part.trim()).filter(Boolean))];
}

function verdictLabel(verdict: ClaimAuditVerdict | null): string {
  if (verdict === "SUPPORTED") return "Supported by this run";
  if (verdict === "CAPABILITY_GAP") return "Capability gap";
  if (verdict === "WITHHELD") return "Withheld";
  return "Pending";
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function VerificationResult({ result }: { result: EvidencePackVerification | null }) {
  if (!result) return null;
  return (
    <div className={`claim-verification ${result.valid ? "valid" : "invalid"}`} role="status">
      <strong>{result.valid ? "Signature and contents verified" : "Verification failed"}</strong>
      <span>{result.valid ? `Key: ${result.key_id || "unknown"}` : result.reason || "Unknown reason"}</span>
    </div>
  );
}

function EvidenceGraph({ audit }: { audit: ClaimAuditSummary }) {
  const graph = audit.evidence_graph;
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  if (!graph || (nodes.length === 0 && edges.length === 0)) {
    return <p className="claim-muted">No server-signed evidence path was produced.</p>;
  }
  return (
    <div className="claim-graph" aria-label="Evidence graph">
      <div>
        <h4>Nodes</h4>
        <ul>
          {nodes.map((node, index) => (
            <li key={String(node.id || index)}>
              <code>{String(node.id || `node-${index + 1}`)}</code>
              <span>{String(node.kind || node.type || "evidence")}</span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h4>Verified links</h4>
        <ul>
          {edges.map((edge, index) => (
            <li key={`${edge.from}-${edge.to}-${index}`}>
              <code>{edge.from}</code> → <code>{edge.to}</code>
              <span>{edge.kind}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function AuditDetail({
  audit,
  busy,
  onCancel,
  onRetry,
  onDelete,
  onRefresh,
}: {
  audit: ClaimAuditSummary;
  busy: boolean;
  onCancel: () => Promise<void>;
  onRetry: () => Promise<void>;
  onDelete: () => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [verification, setVerification] = useState<EvidencePackVerification | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function verifyStored() {
    if (!audit.evidence_pack) return;
    setVerifyBusy(true);
    setVerifyError(null);
    try {
      setVerification(await verifyEvidencePack(audit.evidence_pack.pack_id));
    } catch (error: unknown) {
      setVerifyError(errorMessage(error, "Could not verify the Evidence Pack."));
    } finally {
      setVerifyBusy(false);
    }
  }

  async function verifyUpload(file: File) {
    setVerifyBusy(true);
    setVerifyError(null);
    try {
      setVerification(await verifyEvidencePackFile(file, audit.evidence_pack?.pack_id));
    } catch (error: unknown) {
      setVerifyError(errorMessage(error, "Could not verify the uploaded file."));
    } finally {
      setVerifyBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function downloadPack() {
    if (!audit.evidence_pack) return;
    const blob = await downloadEvidencePack(audit.evidence_pack.pack_id);
    downloadBlob(blob, `claim-audit-${audit.audit_id}.zip`);
  }

  return (
    <article className="claim-detail">
      <header className="claim-detail-header">
        <div>
          <div className="claim-eyebrow">Audit {audit.audit_id.slice(0, 8)}</div>
          <h2>{audit.claim_text}</h2>
        </div>
        <div className="claim-badges">
          <span className={`claim-status status-${audit.lifecycle_status.toLowerCase()}`}>
            {audit.lifecycle_status.replaceAll("_", " ")}
          </span>
          <span className={`claim-verdict verdict-${(audit.scientific_verdict || "pending").toLowerCase()}`}>
            {verdictLabel(audit.scientific_verdict)}
          </span>
        </div>
      </header>

      <div className="claim-warning" role="note">
        {audit.scientific_verdict === "SUPPORTED"
          ? "SUPPORTED means the registered evidence path passed this run. It does not mean peer review or scientific consensus."
          : audit.scientific_verdict === "CAPABILITY_GAP"
            ? "The audit is complete, but a registered dataset, likelihood, or schema is missing. No unsupported conclusion was substituted."
            : "Numerical or scientific support is withheld unless every strong claim has current-run, publication-ready server evidence."}
      </div>

      {audit.error && (
        <div className="claim-error" role="alert">
          <strong>{audit.error_class || "Audit error"}</strong>
          <span>{audit.error}</span>
        </div>
      )}

      <dl className="claim-meta">
        <div><dt>Mode</dt><dd>{audit.mode}</dd></div>
        <div><dt>Source</dt><dd>{audit.source.kind}: {audit.source.value}</dd></div>
        <div><dt>Created</dt><dd>{formatTime(audit.created_at)}</dd></div>
        <div><dt>Completed</dt><dd>{formatTime(audit.completed_at)}</dd></div>
      </dl>

      <section>
        <h3>Claims and verdicts</h3>
        {audit.normalized_claims.length === 0 ? (
          <p className="claim-muted">Claim normalization is not complete yet.</p>
        ) : (
          <div className="claim-results">
            {audit.normalized_claims.map((claim) => (
              <div className={`claim-result verdict-${claim.verdict.toLowerCase()}`} key={claim.claim_id}>
                <div>
                  <span className="claim-result-id">{claim.claim_id}</span>
                  <strong>{verdictLabel(claim.verdict)}</strong>
                </div>
                <p>{claim.text}</p>
                <div
                  className={`claim-parse-coverage coverage-${claim.parse_coverage}`}
                  role={claim.parse_coverage === "unparsed_residual" ? "note" : undefined}
                >
                  <strong>
                    {claim.parse_coverage === "complete"
                      ? "Full claim parsed"
                      : "Unparsed wording remains"}
                  </strong>
                  <span>
                    {claim.parse_coverage === "complete"
                      ? "The verdict still depends on the evidence shown below."
                      : "The residual wording was not evaluated, so the complete claim cannot be supported."}
                  </span>
                </div>
                <small>
                  {claim.supporting_evidence_ids.length > 0
                    ? `${claim.supporting_evidence_ids.length} signed evidence record(s)`
                    : "No qualifying evidence record"}
                </small>
              </div>
            ))}
          </div>
        )}
      </section>

      {audit.capability_gaps.length > 0 && (
        <section>
          <h3>Actionable capability gaps</h3>
          <ul className="claim-gap-list">
            {audit.capability_gaps.map((gap, index) => (
              <li key={String(gap.gap_code || gap.code || index)}>
                <strong>{String(gap.gap_code || gap.code || gap.kind || "unregistered_input")}</strong>
                <span>{String(gap.next_action || gap.message || gap.detail || "A required registered capability is unavailable.")}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3>Evidence graph</h3>
        <EvidenceGraph audit={audit} />
      </section>

      <section className="claim-pack-section">
        <h3>Private Evidence Pack</h3>
        {audit.evidence_pack ? (
          <>
            <p className="claim-muted">
              Finalized with key <code>{audit.evidence_pack.key_id}</code>. A changed file must fail verification.
            </p>
            <div className="claim-actions">
              <button className="btn-primary" onClick={() => { void downloadPack(); }}>Download ZIP</button>
              <button className="btn-secondary" disabled={verifyBusy} onClick={() => { void verifyStored(); }}>
                Verify stored pack
              </button>
              <label className="btn-secondary claim-file-label">
                Verify downloaded file
                <input
                  ref={fileRef}
                  type="file"
                  accept=".zip,application/zip"
                  disabled={verifyBusy}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void verifyUpload(file);
                  }}
                />
              </label>
            </div>
            <VerificationResult result={verification} />
            {verifyError && <p className="claim-inline-error">{verifyError}</p>}
          </>
        ) : (
          <p className="claim-muted">A pack becomes downloadable only after server finalization.</p>
        )}
      </section>

      <footer className="claim-actions claim-detail-actions">
        <button className="btn-secondary" disabled={busy} onClick={() => { void onRefresh(); }}>Refresh</button>
        {audit.can_cancel && (
          <button className="btn-secondary" disabled={busy} onClick={() => { void onCancel(); }}>Cancel</button>
        )}
        {audit.can_retry && (
          <button className="btn-primary" disabled={busy} onClick={() => { void onRetry(); }}>Retry</button>
        )}
        <button className="btn-danger-sm" disabled={busy || audit.can_cancel} onClick={() => { void onDelete(); }}>
          {audit.can_cancel ? "Cancel before deleting" : "Delete audit and pack"}
        </button>
      </footer>
    </article>
  );
}

export default function ClaimAuditPage() {
  const { user, loading: authLoading } = useAuth();
  const [featureState, setFeatureState] = useState<"loading" | "enabled" | "disabled" | "unreachable">("loading");
  const [audits, setAudits] = useState<ClaimAuditSummary[]>([]);
  const [selected, setSelected] = useState<ClaimAuditSummary | null>(null);
  const [claimText, setClaimText] = useState("");
  const [sourceKind, setSourceKind] = useState<ClaimAuditCreatePayload["source"]["kind"]>("doi");
  const [sourceValue, setSourceValue] = useState("");
  const [mode, setMode] = useState<ClaimAuditCreatePayload["mode"]>("audit_only");
  const [evidenceRefs, setEvidenceRefs] = useState("");
  const [datasetHints, setDatasetHints] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async (preferredId?: string) => {
    const response = await listClaimAudits();
    setAudits(response.items);
    const targetId = preferredId || selected?.audit_id;
    const next = response.items.find((audit) => audit.audit_id === targetId) || response.items[0] || null;
    setSelected(next);
  }, [selected?.audit_id]);

  const refreshSelected = useCallback(async () => {
    if (!selected) return;
    const refreshed = await getClaimAudit(selected.audit_id);
    setSelected(refreshed);
    setAudits((current) => current.map((audit) => audit.audit_id === refreshed.audit_id ? refreshed : audit));
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((config) => {
        if (!cancelled) setFeatureState(config.claim_audit_enabled ? "enabled" : "disabled");
      })
      .catch(() => { if (!cancelled) setFeatureState("unreachable"); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!user || featureState !== "enabled") return;
    let cancelled = false;
    void listClaimAudits()
      .then((response) => {
        if (cancelled) return;
        setAudits(response.items);
        setSelected((current) => (
          response.items.find((audit) => audit.audit_id === current?.audit_id) || response.items[0] || null
        ));
      })
      .catch((loadError: unknown) => {
        if (!cancelled) setError(errorMessage(loadError, "Could not load Claim Audit history."));
      });
    return () => { cancelled = true; };
  }, [featureState, user]);

  useEffect(() => {
    if (!selected || TERMINAL_STATES.has(selected.lifecycle_status)) return;
    const timer = window.setInterval(() => { void refreshSelected(); }, 2500);
    return () => window.clearInterval(timer);
  }, [refreshSelected, selected]);

  const canSubmit = useMemo(() => (
    claimText.trim().length > 0 && sourceValue.trim().length > 0 && !busy
  ), [busy, claimText, sourceValue]);

  async function submitAudit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createClaimAudit({
        claim_text: claimText.trim(),
        source: { kind: sourceKind, value: sourceValue.trim() },
        evidence_input_refs: splitValues(evidenceRefs),
        dataset_hints: splitValues(datasetHints),
        mode,
      });
      await loadList(created.audit_id);
    } catch (submitError: unknown) {
      setError(errorMessage(submitError, "Could not start the Claim Audit."));
    } finally {
      setBusy(false);
    }
  }

  async function mutate(action: () => Promise<ClaimAuditSummary | void>, preferredId?: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      await loadList((result as ClaimAuditSummary | undefined)?.audit_id || preferredId);
    } catch (mutationError: unknown) {
      setError(errorMessage(mutationError, "The audit action failed."));
    } finally {
      setBusy(false);
    }
  }

  if (authLoading || featureState === "loading") {
    return <div className="claim-page claim-empty">Loading Claim Audit…</div>;
  }
  if (!user) {
    return (
      <div className="claim-page claim-empty">
        <h1>Claim Audit</h1>
        <p>Sign in to create private audits and Evidence Packs.</p>
        <Link className="btn-primary" to="/auth">Sign in</Link>
      </div>
    );
  }
  if (featureState !== "enabled") {
    return (
      <div className="claim-page claim-empty">
        <h1>Claim Audit is not open yet</h1>
        <p>
          The workflow remains behind its production flag until P0 and the 14-day Daily evidence gate pass.
          No weaker audit is substituted while it is closed.
        </p>
      </div>
    );
  }

  return (
    <div className="claim-page">
      <header className="claim-page-header">
        <div>
          <div className="claim-eyebrow">Server-verified scientific evidence</div>
          <h1>Claim Audit</h1>
          <p>Check a scientific claim against registered evidence, then export a signed, private Evidence Pack.</p>
        </div>
        <div className="claim-safety-note">
          <strong>Fail closed</strong>
          <span>Missing current-run evidence produces WITHHELD or CAPABILITY_GAP, never a guessed result.</span>
        </div>
      </header>

      {error && <div className="claim-error" role="alert">{error}</div>}

      <div className="claim-workspace">
        <aside>
          <form className="claim-create" onSubmit={submitAudit}>
            <h2>New audit</h2>
            <label>
              Scientific claim
              <textarea
                value={claimText}
                onChange={(event) => setClaimText(event.target.value)}
                rows={6}
                maxLength={20_000}
                required
                placeholder="Example: DESI plus CMB and supernova data prefer evolving dark energy."
              />
            </label>
            <div className="claim-form-row">
              <label>
                Source type
                <select value={sourceKind} onChange={(event) => setSourceKind(event.target.value as typeof sourceKind)}>
                  <option value="doi">DOI</option>
                  <option value="arxiv">arXiv</option>
                  <option value="bibcode">Bibcode</option>
                  <option value="url">Allowlisted URL</option>
                </select>
              </label>
              <label>
                Mode
                <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
                  <option value="audit_only">Audit existing evidence</option>
                  <option value="execute_registered">Execute registered workflow</option>
                </select>
              </label>
            </div>
            <label>
              Source identifier
              <input
                value={sourceValue}
                onChange={(event) => setSourceValue(event.target.value)}
                required
                placeholder={sourceKind === "doi" ? "10.1234/example" : sourceKind === "arxiv" ? "2501.01234" : "Pinned identifier"}
              />
            </label>
            <details>
              <summary>Registered evidence inputs</summary>
              <label>
                Research job IDs, comma or line separated
                <textarea value={evidenceRefs} onChange={(event) => setEvidenceRefs(event.target.value)} rows={2} />
              </label>
              <label>
                Dataset registry keys, comma or line separated
                <textarea value={datasetHints} onChange={(event) => setDatasetHints(event.target.value)} rows={2} />
              </label>
            </details>
            <button className="btn-primary" type="submit" disabled={!canSubmit}>
              {busy ? "Starting…" : "Start Claim Audit"}
            </button>
          </form>

          <section className="claim-history">
            <div className="claim-history-heading">
              <h2>Research history</h2>
              <button className="btn-secondary" disabled={busy} onClick={() => { void loadList(); }}>Refresh</button>
            </div>
            {audits.length === 0 ? (
              <p className="claim-muted">No Claim Audits yet.</p>
            ) : (
              <div className="claim-history-list">
                {audits.map((audit) => (
                  <button
                    key={audit.audit_id}
                    className={selected?.audit_id === audit.audit_id ? "active" : ""}
                    onClick={() => setSelected(audit)}
                  >
                    <span>{audit.claim_text}</span>
                    <small>{audit.lifecycle_status} · {verdictLabel(audit.scientific_verdict)}</small>
                  </button>
                ))}
              </div>
            )}
          </section>
        </aside>

        <main>
          {selected ? (
            <AuditDetail
              audit={selected}
              busy={busy}
              onRefresh={refreshSelected}
              onCancel={() => mutate(() => cancelClaimAudit(selected.audit_id), selected.audit_id)}
              onRetry={() => mutate(() => retryClaimAudit(selected.audit_id), selected.audit_id)}
              onDelete={() => {
                if (!window.confirm("Delete this private audit and its Evidence Pack?")) return Promise.resolve();
                return mutate(() => deleteClaimAudit(selected.audit_id));
              }}
            />
          ) : (
            <div className="claim-detail claim-empty">
              <h2>No audit selected</h2>
              <p>Create an audit or restore one from Research History.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
