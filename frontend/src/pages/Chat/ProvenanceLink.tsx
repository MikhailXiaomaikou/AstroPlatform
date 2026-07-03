// Provenance-ledger entry point on tool-result cards (2026-07-03).
//
// Every chat tool result carries a reproducibility envelope with a run_id;
// the backend now registers that envelope in the provenance ledger
// (app/services/provenance), so /api/provenance/{run_id}/* can answer for
// it. This link is the first frontend caller of that API.
//
// HONESTY: the ledger is in-memory per backend process. An empty lineage is
// a real, expected state (backend restarted, or the run happened in another
// process) and is reported as such — never dressed up as data.
import { useState } from "react";
import api from "../../api/client";
import { useI18n } from "../../i18n";

type LedgerState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "recorded"; nodeCount: number }
  | { kind: "empty" }
  | { kind: "error" };

export function ProvenanceLink({ result }: { result?: Record<string, unknown> }) {
  const { t } = useI18n();
  const [state, setState] = useState<LedgerState>({ kind: "idle" });

  const envelope = result?.reproducibility;
  const runId = envelope && typeof envelope === "object"
    ? String((envelope as Record<string, unknown>).run_id || "")
    : "";
  if (!runId) return null;

  const lookup = async () => {
    setState({ kind: "loading" });
    try {
      const { data } = await api.get(`/api/provenance/${encodeURIComponent(runId)}/lineage`);
      const nodes = Array.isArray(data?.nodes) ? data.nodes : [];
      setState(nodes.length > 0 ? { kind: "recorded", nodeCount: nodes.length } : { kind: "empty" });
    } catch {
      setState({ kind: "error" });
    }
  };

  const downloadIvoa = async () => {
    try {
      const { data } = await api.get(
        `/api/provenance/${encodeURIComponent(runId)}/export/ivoa`,
        { responseType: "blob" },
      );
      const url = URL.createObjectURL(data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `provenance-${runId.slice(0, 8)}.xml`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setState({ kind: "error" });
    }
  };

  return (
    <div className="chat-provenance-link" style={{ marginTop: 4, fontSize: "0.74rem" }}>
      <button
        className="btn-ghost btn-small"
        style={{ fontSize: "0.74rem", padding: "1px 8px" }}
        onClick={() => { void lookup(); }}
        disabled={state.kind === "loading"}
      >
        🔏 {t("chat.provenance.button")}
      </button>
      {state.kind === "loading" && (
        <span style={{ marginLeft: 6, color: "var(--color-text-secondary, #666)" }}>
          {t("chat.provenance.loading")}
        </span>
      )}
      {state.kind === "recorded" && (
        <span style={{ marginLeft: 6, color: "#1b5e20" }}>
          {t("chat.provenance.recorded").replace("{n}", String(state.nodeCount))}
          {" · "}
          <a
            href="#"
            onClick={(event) => { event.preventDefault(); void downloadIvoa(); }}
          >
            {t("chat.provenance.download_ivoa")}
          </a>
        </span>
      )}
      {state.kind === "empty" && (
        <span style={{ marginLeft: 6, color: "var(--color-text-secondary, #666)" }}>
          {t("chat.provenance.empty")}
        </span>
      )}
      {state.kind === "error" && (
        <span style={{ marginLeft: 6, color: "#b00020" }}>
          {t("chat.provenance.error")}
        </span>
      )}
    </div>
  );
}
