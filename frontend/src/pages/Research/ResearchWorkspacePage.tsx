import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  archiveResearchWorkspace,
  cancelClaimAudit,
  createClaimAuditRevision,
  createSourceDocument,
  createWorkerEnrollment,
  createWorkspaceClaimAudit,
  deleteClaimAudit,
  downloadEvidencePack,
  getResearchWorkspace,
  getRuntimeConfig,
  getSourceDocumentContent,
  getSourceDocumentTables,
  listScientificReviewQueue,
  listSourceDocuments,
  listWorkerNodes,
  listWorkspaceClaimAudits,
  retryClaimAudit,
  retrySourceDocument,
  revokeWorkerNode,
  submitClaimAuditReview,
  verifyEvidencePack,
  type ClaimAuditReviewBinding,
  type ClaimAuditReviewDecision,
  type ClaimAuditSummary,
  type RuntimeConfig,
  type SourceCandidate,
  type SourceDocumentContent,
  type SourceDocumentSummary,
  type SourceDocumentTables,
  type SourceExtractionSummary,
  type ResearchWorkspaceSummary,
  type WorkerEnrollment,
  type WorkerNodeSummary,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../i18n";
import "./ResearchWorkspace.css";

type FeatureState = "loading" | "enabled" | "disabled" | "unreachable";
type WorkspaceTab = "overview" | "sources" | "claims" | "runs" | "evidence";
type ReviewerAccess = "loading" | "allowed" | "denied" | "error";

const WORKFLOW_KEY = "union3_flat_lcdm_sn_only_v1" as const;

interface CandidateRecord {
  document: SourceDocumentSummary;
  extraction: SourceExtractionSummary;
  candidate: SourceCandidate;
}

interface SourceDetailsState {
  content?: SourceDocumentContent;
  tables?: SourceDocumentTables;
  loading?: boolean;
  error?: string;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "response" in error) {
    const detail = (error as {
      response?: { data?: { detail?: string | { message?: string } } };
    }).response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object" && typeof detail.message === "string") {
      return detail.message;
    }
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function responseStatus(error: unknown): number | undefined {
  return (error as { response?: { status?: number } })?.response?.status;
}

function isHash(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function reviewBindingFor(audit: ClaimAuditSummary): ClaimAuditReviewBinding | null {
  const provided = audit.review_binding;
  const evidence = audit.review_evidence;
  const candidate = audit.normalized_claims.find(
    (item) => item.candidate_id === audit.candidate_id,
  );
  if (
    provided
    && evidence
    && audit.source_document_id === provided.source_document_id
    && audit.source_extraction_id === provided.source_extraction_id
    && audit.candidate_id === provided.candidate_id
    && isHash(provided.claim_hash)
    && isHash(provided.source_hash)
    && provided.anchor_ids.length > 0
    && evidence.source_document_hash === provided.source_hash
    && candidate?.claim_hash === provided.claim_hash
    && Array.isArray(candidate.source_anchor_ids)
    && candidate.source_anchor_ids.length === provided.anchor_ids.length
    && candidate.source_anchor_ids.every(
      (anchorId, index) => anchorId === provided.anchor_ids[index],
    )
    && evidence.anchors.length === provided.anchor_ids.length
    && evidence.anchors.every(
      (anchor, index) => anchor.anchor_id === provided.anchor_ids[index],
    )
  ) {
    return provided;
  }
  return null;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function verdictClass(audit: ClaimAuditSummary): string {
  return `verdict-${(audit.scientific_verdict || "pending").toLowerCase()}`;
}

function statusText(value: string): string {
  return value.replaceAll("_", " ");
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

function TabButton({
  active,
  label,
  tab,
  onSelect,
}: {
  active: boolean;
  label: string;
  tab: WorkspaceTab;
  onSelect: (tab: WorkspaceTab) => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-controls={`research-panel-${tab}`}
      id={`research-tab-${tab}`}
      className={active ? "active" : ""}
      onClick={() => onSelect(tab)}
    >
      {label}
    </button>
  );
}

export default function ResearchWorkspacePage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const { t } = useI18n();
  const [featureState, setFeatureState] = useState<FeatureState>("loading");
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [workspace, setWorkspace] = useState<ResearchWorkspaceSummary | null>(null);
  const [sources, setSources] = useState<SourceDocumentSummary[]>([]);
  const [audits, setAudits] = useState<ClaimAuditSummary[]>([]);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("overview");
  const [busy, setBusy] = useState(false);
  const [runningCandidateId, setRunningCandidateId] = useState<string | null>(null);
  const [auditActionId, setAuditActionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [verification, setVerification] = useState<Record<string, string>>({});
  const [expandedSourceId, setExpandedSourceId] = useState<string | null>(null);
  const [sourceDetails, setSourceDetails] = useState<Record<string, SourceDetailsState>>({});
  const [workerNodes, setWorkerNodes] = useState<WorkerNodeSummary[]>([]);
  const [workerEnrollment, setWorkerEnrollment] = useState<WorkerEnrollment | null>(null);
  const [workerBusy, setWorkerBusy] = useState(false);
  const [workerError, setWorkerError] = useState<string | null>(null);
  const [reviewerAccess, setReviewerAccess] = useState<ReviewerAccess>("loading");
  const [reviewQueue, setReviewQueue] = useState<ClaimAuditSummary[]>([]);
  const [reviewComments, setReviewComments] = useState<Record<string, string>>({});
  const [reviewingAuditId, setReviewingAuditId] = useState<string | null>(null);

  const loadWorkspace = useCallback(async () => {
    if (!workspaceId) return;
    const [workspaceResponse, sourceResponse] = await Promise.all([
      getResearchWorkspace(workspaceId),
      listSourceDocuments(workspaceId),
    ]);
    setWorkspace(workspaceResponse);
    setSources(sourceResponse.items);

    if (config?.claim_audit_enabled) {
      const auditResponse = await listWorkspaceClaimAudits(workspaceId);
      setAudits(auditResponse.items);
    } else {
      setAudits([]);
    }
  }, [config?.claim_audit_enabled, workspaceId]);

  const loadWorkerNodes = useCallback(async () => {
    const response = await listWorkerNodes();
    setWorkerNodes(response.nodes);
  }, []);

  const loadReviewQueue = useCallback(async () => {
    try {
      const response = await listScientificReviewQueue();
      setReviewQueue(response.items);
      setReviewerAccess("allowed");
    } catch (reviewError: unknown) {
      if (responseStatus(reviewError) === 403) {
        setReviewQueue([]);
        setReviewerAccess("denied");
        return;
      }
      setReviewerAccess("error");
      throw reviewError;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((runtimeConfig) => {
        if (cancelled) return;
        setConfig(runtimeConfig);
        setFeatureState(
          runtimeConfig.research_workspace_enabled === true ? "enabled" : "disabled",
        );
      })
      .catch(() => {
        if (!cancelled) setFeatureState("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!user || featureState !== "enabled" || !workspaceId || !config) return;
    let cancelled = false;
    setBusy(true);
    void loadWorkspace()
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(errorMessage(loadError, t("research.error.load_workspace")));
        }
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [config, featureState, loadWorkspace, t, user, workspaceId]);

  useEffect(() => {
    if (!user || featureState !== "enabled" || !config) return;
    let cancelled = false;

    if (config.local_science_worker_enabled) {
      void loadWorkerNodes().catch((loadError: unknown) => {
        if (!cancelled) {
          setWorkerError(errorMessage(loadError, t("research.error.load_workers")));
        }
      });
    } else {
      setWorkerNodes([]);
      setWorkerEnrollment(null);
    }

    if (config.claim_audit_enabled) {
      void loadReviewQueue().catch(() => undefined);
    } else {
      setReviewerAccess("denied");
      setReviewQueue([]);
    }
    return () => {
      cancelled = true;
    };
  }, [config, featureState, loadReviewQueue, loadWorkerNodes, t, user]);

  const hasActiveWork = useMemo(
    () => sources.some((source) => ["QUEUED", "RUNNING"].includes(source.lifecycle_status))
      || audits.some((audit) => ["QUEUED", "RUNNING"].includes(audit.lifecycle_status)),
    [audits, sources],
  );

  useEffect(() => {
    if (!hasActiveWork || !user || featureState !== "enabled") return;
    const interval = window.setInterval(() => {
      void loadWorkspace().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [featureState, hasActiveWork, loadWorkspace, user]);

  const candidates = useMemo<CandidateRecord[]>(() => {
    const latestByIdentifier = new Map<string, SourceDocumentSummary>();
    for (const source of sources) {
      const current = latestByIdentifier.get(source.canonical_identifier);
      if (!current || source.version > current.version) {
        latestByIdentifier.set(source.canonical_identifier, source);
      }
    }
    return [...latestByIdentifier.values()].flatMap((document) => {
      const extraction = document.extraction;
      if (!extraction || document.lifecycle_status !== "COMPLETED") return [];
      return extraction.extraction_payload.candidates.map((candidate) => ({
        document,
        extraction,
        candidate,
      }));
    });
  }, [sources]);

  const evidenceAudits = audits.filter((audit) => Boolean(audit.evidence_pack));
  const workspaceActive = workspace?.status === "ACTIVE";
  const readerEnabled = config?.arxiv_reader_enabled === true;
  const evidenceV2Enabled = config?.evidence_pack_v2_enabled === true;
  const executionEnabled = Boolean(
    workspaceActive
      && readerEnabled
      && evidenceV2Enabled
      && config?.claim_audit_enabled
      && config?.local_science_worker_enabled
      && config?.union3_reproduction_enabled,
  );

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      await loadWorkspace();
      if (config?.local_science_worker_enabled) await loadWorkerNodes();
      if (reviewerAccess === "allowed") await loadReviewQueue();
    } catch (loadError: unknown) {
      setError(errorMessage(loadError, t("research.error.load_workspace")));
    } finally {
      setBusy(false);
    }
  }

  async function addUnion3Source() {
    if (!workspaceId || !workspaceActive || !readerEnabled || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await createSourceDocument(workspaceId, {
        source_profile_key: "union3_arxiv_v1",
        identifier: "2311.12098v4",
      });
      await loadWorkspace();
      setNotice(t("research.source_added"));
    } catch (sourceError: unknown) {
      setError(errorMessage(sourceError, t("research.error.add_source")));
    } finally {
      setBusy(false);
    }
  }

  async function retrySource(sourceDocumentId: string) {
    if (!workspaceId || !workspaceActive || !readerEnabled || busy) return;
    setBusy(true);
    setError(null);
    try {
      await retrySourceDocument(workspaceId, sourceDocumentId);
      await loadWorkspace();
    } catch (sourceError: unknown) {
      setError(errorMessage(sourceError, t("research.error.retry_source")));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSourceDetails(sourceDocumentId: string) {
    if (expandedSourceId === sourceDocumentId) {
      setExpandedSourceId(null);
      return;
    }
    setExpandedSourceId(sourceDocumentId);
    if (sourceDetails[sourceDocumentId]?.content || sourceDetails[sourceDocumentId]?.loading) {
      return;
    }
    setSourceDetails((current) => ({
      ...current,
      [sourceDocumentId]: { loading: true },
    }));
    try {
      const [content, tables] = await Promise.all([
        getSourceDocumentContent(sourceDocumentId),
        getSourceDocumentTables(sourceDocumentId),
      ]);
      setSourceDetails((current) => ({
        ...current,
        [sourceDocumentId]: { content, tables },
      }));
    } catch (detailsError: unknown) {
      setSourceDetails((current) => ({
        ...current,
        [sourceDocumentId]: {
          error: errorMessage(detailsError, t("research.error.load_source_details")),
        },
      }));
    }
  }

  async function createEnrollment() {
    if (!config?.local_science_worker_enabled || workerBusy) return;
    setWorkerBusy(true);
    setWorkerError(null);
    try {
      setWorkerEnrollment(await createWorkerEnrollment());
    } catch (enrollmentError: unknown) {
      setWorkerError(errorMessage(enrollmentError, t("research.error.create_enrollment")));
    } finally {
      setWorkerBusy(false);
    }
  }

  async function revokeNode(nodeId: string) {
    if (workerBusy) return;
    setWorkerBusy(true);
    setWorkerError(null);
    try {
      await revokeWorkerNode(nodeId);
      await loadWorkerNodes();
      setNotice(t("research.worker_revoked"));
    } catch (revokeError: unknown) {
      setWorkerError(errorMessage(revokeError, t("research.error.revoke_worker")));
    } finally {
      setWorkerBusy(false);
    }
  }

  async function startRegisteredWorkflow(record: CandidateRecord) {
    if (!workspaceId || !workspaceActive || !executionEnabled || runningCandidateId) return;
    setRunningCandidateId(record.candidate.candidate_id);
    setError(null);
    setNotice(null);
    try {
      const audit = await createWorkspaceClaimAudit(workspaceId, {
        source_document_id: record.document.source_document_id,
        candidate_id: record.candidate.candidate_id,
        workflow_key: WORKFLOW_KEY,
      });
      setAudits((current) => [audit, ...current.filter((item) => item.audit_id !== audit.audit_id)]);
      setActiveTab("runs");
      setNotice(t("research.run_started"));
    } catch (runError: unknown) {
      setError(errorMessage(runError, t("research.error.start_run")));
    } finally {
      setRunningCandidateId(null);
    }
  }

  async function cancelAudit(auditId: string) {
    if (auditActionId) return;
    setAuditActionId(auditId);
    setError(null);
    try {
      const updated = await cancelClaimAudit(auditId);
      setAudits((current) => current.map((item) => (
        item.audit_id === auditId ? updated : item
      )));
      setNotice(t("research.run_cancelled"));
    } catch (cancelError: unknown) {
      setError(errorMessage(cancelError, t("research.error.cancel_run")));
    } finally {
      setAuditActionId(null);
    }
  }

  async function retryAudit(auditId: string) {
    if (auditActionId) return;
    setAuditActionId(auditId);
    setError(null);
    try {
      const updated = await retryClaimAudit(auditId);
      setAudits((current) => current.map((item) => (
        item.audit_id === auditId ? updated : item
      )));
      setNotice(t("research.run_retried"));
    } catch (retryError: unknown) {
      setError(errorMessage(retryError, t("research.error.retry_run")));
    } finally {
      setAuditActionId(null);
    }
  }

  async function reviseAudit(auditId: string) {
    if (auditActionId) return;
    setAuditActionId(auditId);
    setError(null);
    try {
      const revision = await createClaimAuditRevision(auditId);
      setAudits((current) => [
        revision,
        ...current.filter((item) => item.audit_id !== revision.audit_id),
      ]);
      setNotice(t("research.run_revised"));
    } catch (revisionError: unknown) {
      setError(errorMessage(revisionError, t("research.error.revise_run")));
    } finally {
      setAuditActionId(null);
    }
  }

  async function deleteAudit(auditId: string) {
    if (auditActionId || !window.confirm(t("research.delete_run_confirm"))) return;
    setAuditActionId(auditId);
    setError(null);
    try {
      await deleteClaimAudit(auditId);
      setAudits((current) => current.filter((item) => item.audit_id !== auditId));
      setNotice(t("research.run_deleted"));
    } catch (deleteError: unknown) {
      setError(errorMessage(deleteError, t("research.error.delete_run")));
    } finally {
      setAuditActionId(null);
    }
  }

  async function submitReview(
    audit: ClaimAuditSummary,
    decision: ClaimAuditReviewDecision,
  ) {
    const binding = reviewBindingFor(audit);
    if (!binding || reviewingAuditId) return;
    setReviewingAuditId(audit.audit_id);
    setError(null);
    try {
      await submitClaimAuditReview(audit.audit_id, {
        ...binding,
        decision,
        comment: reviewComments[audit.audit_id]?.trim() || "",
      });
      setReviewQueue((current) => current.filter((item) => item.audit_id !== audit.audit_id));
      setNotice(t("research.review_submitted"));
    } catch (reviewError: unknown) {
      setError(errorMessage(reviewError, t("research.error.submit_review")));
    } finally {
      setReviewingAuditId(null);
    }
  }

  async function downloadPack(audit: ClaimAuditSummary) {
    if (!audit.evidence_pack) return;
    setError(null);
    try {
      const blob = await downloadEvidencePack(audit.evidence_pack.pack_id);
      downloadBlob(blob, `standard-astro-evidence-${audit.audit_id}.zip`);
    } catch (packError: unknown) {
      setError(errorMessage(packError, t("research.error.download_pack")));
    }
  }

  async function verifyPack(audit: ClaimAuditSummary) {
    if (!audit.evidence_pack) return;
    setError(null);
    try {
      const result = await verifyEvidencePack(audit.evidence_pack.pack_id);
      setVerification((current) => ({
        ...current,
        [audit.evidence_pack!.pack_id]: result.valid
          ? t("research.pack_verified")
          : result.reason || t("research.pack_invalid"),
      }));
    } catch (packError: unknown) {
      setError(errorMessage(packError, t("research.error.verify_pack")));
    }
  }

  async function archiveWorkspace() {
    if (!workspaceId || busy) return;
    if (!window.confirm(t("research.archive_confirm"))) return;
    setBusy(true);
    setError(null);
    try {
      await archiveResearchWorkspace(workspaceId);
      navigate("/research");
    } catch (archiveError: unknown) {
      setError(errorMessage(archiveError, t("research.error.archive")));
      setBusy(false);
    }
  }

  if (authLoading || featureState === "loading") {
    return <div className="research-page research-state">{t("research.loading")}</div>;
  }
  if (!user) {
    return (
      <div className="research-page research-state">
        <h1>{t("research.title")}</h1>
        <p>{t("research.sign_in_body")}</p>
        <Link className="btn-primary" to="/auth">{t("nav.sign_in")}</Link>
      </div>
    );
  }
  if (featureState !== "enabled") {
    return (
      <div className="research-page research-state" data-testid="research-disabled">
        <h1>{t("research.closed_title")}</h1>
        <p>{t("research.closed_body")}</p>
      </div>
    );
  }
  if (!workspaceId) {
    return (
      <div className="research-page research-state">
        <h1>{t("research.not_found")}</h1>
        <Link to="/research">{t("research.back")}</Link>
      </div>
    );
  }
  if (!workspace && busy) {
    return <div className="research-page research-state">{t("research.loading_workspace")}</div>;
  }
  if (!workspace) {
    return (
      <div className="research-page research-state">
        <h1>{t("research.not_found")}</h1>
        {error && <p role="alert">{error}</p>}
        <Link to="/research">{t("research.back")}</Link>
      </div>
    );
  }

  const tabs: Array<{ id: WorkspaceTab; label: string }> = [
    { id: "overview", label: t("research.tab.overview") },
    { id: "sources", label: t("research.tab.sources") },
    { id: "claims", label: t("research.tab.claims") },
    { id: "runs", label: t("research.tab.runs") },
    { id: "evidence", label: t("research.tab.evidence") },
  ];

  return (
    <div className="research-page research-workspace-page">
      <Link className="research-back-link" to="/research">← {t("research.back")}</Link>
      <header className="research-workspace-header">
        <div>
          <p className="research-eyebrow">{t("research.private_workspace")}</p>
          <h1>{workspace.title}</h1>
          <p>{workspace.description || t("research.no_description")}</p>
        </div>
        <div className="research-header-actions">
          <span className={`research-badge status-${workspace.status.toLowerCase()}`}>
            {workspace.status}
          </span>
          <button className="btn-secondary" disabled={busy} onClick={() => { void refresh(); }}>
            {t("research.refresh")}
          </button>
          <button className="btn-danger-sm" disabled={busy || !workspaceActive} onClick={() => { void archiveWorkspace(); }}>
            {t("research.archive")}
          </button>
        </div>
      </header>

      {error && <div className="research-alert error" role="alert">{error}</div>}
      {notice && <div className="research-alert success" role="status">{notice}</div>}
      {!workspaceActive && (
        <div className="research-alert info" role="note">
          <strong>{t("research.archived_read_only_title")}</strong>
          <span>{t("research.archived_read_only_body")}</span>
        </div>
      )}

      <nav className="research-tabs" role="tablist" aria-label={t("research.sections")}>
        {tabs.map((tab) => (
          <TabButton
            key={tab.id}
            active={activeTab === tab.id}
            label={tab.label}
            tab={tab.id}
            onSelect={setActiveTab}
          />
        ))}
      </nav>

      <section
        id={`research-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`research-tab-${activeTab}`}
        className="research-tab-panel"
      >
        {activeTab === "overview" && (
          <div className="research-overview">
            <div className="research-stat-grid">
              <article><strong>{sources.length}</strong><span>{t("research.stat.sources")}</span></article>
              <article><strong>{candidates.length}</strong><span>{t("research.stat.claims")}</span></article>
              <article><strong>{audits.length}</strong><span>{t("research.stat.runs")}</span></article>
              <article><strong>{evidenceAudits.length}</strong><span>{t("research.stat.packs")}</span></article>
            </div>
            <div className="research-overview-grid">
              <article className="research-panel">
                <p className="research-kicker">{t("research.flow_label")}</p>
                <h2>{t("research.flow_title")}</h2>
                <ol className="research-flow-list">
                  <li><span>1</span><div><strong>{t("research.flow.source")}</strong><p>{t("research.flow.source_body")}</p></div></li>
                  <li><span>2</span><div><strong>{t("research.flow.claim")}</strong><p>{t("research.flow.claim_body")}</p></div></li>
                  <li><span>3</span><div><strong>{t("research.flow.run")}</strong><p>{t("research.flow.run_body")}</p></div></li>
                  <li><span>4</span><div><strong>{t("research.flow.review")}</strong><p>{t("research.flow.review_body")}</p></div></li>
                  <li><span>5</span><div><strong>{t("research.flow.pack")}</strong><p>{t("research.flow.pack_body")}</p></div></li>
                </ol>
              </article>
              <aside className="research-panel research-boundary-card">
                <p className="research-kicker">{t("research.boundary_label")}</p>
                <h2>{t("research.boundary_title")}</h2>
                <p>{t("research.boundary_body")}</p>
                <ul>
                  <li>{t("research.boundary.one")}</li>
                  <li>{t("research.boundary.two")}</li>
                  <li>{t("research.boundary.three")}</li>
                </ul>
              </aside>
            </div>
            <div className="research-control-grid">
              {config?.local_science_worker_enabled && (
                <article className="research-panel research-worker-panel">
                  <div className="research-panel-heading">
                    <div>
                      <p className="research-kicker">{t("research.worker_label")}</p>
                      <h2>{t("research.worker_title")}</h2>
                      <p>{t("research.worker_body")}</p>
                    </div>
                    <button
                      className="btn-primary"
                      disabled={workerBusy}
                      onClick={() => { void createEnrollment(); }}
                    >
                      {workerBusy ? t("research.worker_creating") : t("research.worker_create_code")}
                    </button>
                  </div>
                  {workerError && <p className="research-inline-error" role="alert">{workerError}</p>}
                  {workerEnrollment && (
                    <div className="research-enrollment" role="status">
                      <strong>{t("research.worker_code_once")}</strong>
                      <code>{workerEnrollment.enrollment_code}</code>
                      <span>{t("research.worker_code_expires")} {formatTime(workerEnrollment.expires_at)}</span>
                      <pre>ASTRO_WORKER_IMAGE=&apos;ghcr.io/mikhailxiaomaikou/standard-astro/science-worker@sha256:&lt;release-digest&gt;&apos; GIT_COMMIT=&apos;&lt;40-character-release-commit&gt;&apos; ./deploy/start-signed-worker.sh enroll &lt;one-time-code&gt; --control-plane https://&lt;control-center&gt; --name &quot;My science worker&quot;</pre>
                      <button className="btn-secondary" onClick={() => setWorkerEnrollment(null)}>
                        {t("research.worker_hide_code")}
                      </button>
                    </div>
                  )}
                  <div className="research-command-list" aria-label={t("research.worker_commands")}>
                    <span>{t("research.worker_release_manifest_note")}</span>
                    <code>ASTRO_WORKER_IMAGE=&apos;...@sha256:&lt;release-digest&gt;&apos; GIT_COMMIT=&apos;&lt;release-commit&gt;&apos; ./deploy/start-signed-worker.sh start</code>
                    <code>ASTRO_WORKER_IMAGE=&apos;...@sha256:&lt;release-digest&gt;&apos; GIT_COMMIT=&apos;&lt;release-commit&gt;&apos; ./deploy/start-signed-worker.sh status</code>
                  </div>
                  {workerNodes.length === 0 ? (
                    <p className="research-panel-empty">{t("research.worker_none")}</p>
                  ) : (
                    <div className="research-node-list">
                      {workerNodes.map((node) => (
                        <div className="research-node-row" key={node.node_id}>
                          <div>
                            <strong>{node.name}</strong>
                            <span>
                              {node.online ? t("research.worker_online") : t("research.worker_offline")}
                              {" · "}{statusText(node.status)}
                            </span>
                            <small>{t("research.worker_last_seen")} {formatTime(node.last_seen_at)}</small>
                          </div>
                          {node.status !== "REVOKED" && (
                            <button
                              className="btn-danger-sm"
                              disabled={workerBusy}
                              onClick={() => { void revokeNode(node.node_id); }}
                            >
                              {t("research.worker_revoke")}
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              )}

              {reviewerAccess === "allowed" && (
                <article className="research-panel research-review-panel">
                  <div className="research-panel-heading">
                    <div>
                      <p className="research-kicker">{t("research.reviewer_label")}</p>
                      <h2>{t("research.reviewer_title")}</h2>
                      <p>{t("research.reviewer_body")}</p>
                    </div>
                  </div>
                  {reviewQueue.length === 0 ? (
                    <p className="research-panel-empty">{t("research.reviewer_empty")}</p>
                  ) : (
                    <div className="research-review-list">
                      {reviewQueue.map((audit) => {
                        const binding = audit.review_evidence ? reviewBindingFor(audit) : null;
                        return (
                          <div className="research-review-row" key={audit.audit_id}>
                            <strong>{audit.claim_text}</strong>
                            <span>{t("research.review_machine_gate")} {audit.machine_support_eligible ? "PASS" : "WITHHELD"}</span>
                            {audit.review_evidence && (
                              <div className="research-review-evidence">
                                <a href={audit.review_evidence.source_url} target="_blank" rel="noreferrer">
                                  {t("research.review_open_source")} · {audit.review_evidence.canonical_identifier}
                                </a>
                                <code>SHA-256 {audit.review_evidence.source_document_hash}</code>
                                {audit.review_evidence.anchors.map((anchor) => (
                                  <figure key={anchor.anchor_id}>
                                    <figcaption>
                                      {String(anchor.locator.role || t("research.source_anchor"))}
                                      {" · "}{t("research.pdf_page")} {String(anchor.locator.pdf_page_label || "—")}
                                    </figcaption>
                                    <blockquote>{anchor.raw_text}</blockquote>
                                  </figure>
                                ))}
                                {audit.review_evidence.limitations.length > 0 && (
                                  <ul>
                                    {audit.review_evidence.limitations.map((item) => <li key={item}>{item}</li>)}
                                  </ul>
                                )}
                              </div>
                            )}
                            <label>
                              {t("research.review_comment")}
                              <textarea
                                value={reviewComments[audit.audit_id] ?? ""}
                                onChange={(event) => setReviewComments((current) => ({
                                  ...current,
                                  [audit.audit_id]: event.target.value,
                                }))}
                                rows={3}
                                maxLength={4000}
                              />
                            </label>
                            {!binding && (
                              <p className="research-inline-error" role="note">
                                {t("research.review_binding_missing")}
                              </p>
                            )}
                            <div className="research-card-actions">
                              <button
                                className="btn-primary"
                                disabled={!binding || reviewingAuditId !== null}
                                onClick={() => { void submitReview(audit, "APPROVED"); }}
                              >
                                {reviewingAuditId === audit.audit_id
                                  ? t("research.review_submitting")
                                  : t("research.review_approve")}
                              </button>
                              <button
                                className="btn-secondary"
                                disabled={!binding || reviewingAuditId !== null}
                                onClick={() => { void submitReview(audit, "CHANGES_REQUESTED"); }}
                              >
                                {t("research.review_request_changes")}
                              </button>
                              <button
                                className="btn-danger-sm"
                                disabled={!binding || reviewingAuditId !== null}
                                onClick={() => { void submitReview(audit, "REJECTED"); }}
                              >
                                {t("research.review_reject")}
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </article>
              )}
            </div>
          </div>
        )}

        {activeTab === "sources" && (
          <div className="research-section-stack">
            <div className="research-section-heading">
              <div><h2>{t("research.sources_title")}</h2><p>{t("research.sources_body")}</p></div>
              {readerEnabled && workspaceActive && (
                <button className="btn-primary" disabled={busy} onClick={() => { void addUnion3Source(); }}>
                  {busy ? t("research.adding_source") : t("research.add_union3")}
                </button>
              )}
            </div>
            {!readerEnabled && (
              <div className="research-alert info" role="note">
                <strong>{t("research.reader_closed_title")}</strong>
                <span>{t("research.reader_closed_body")}</span>
              </div>
            )}
            {sources.length === 0 ? (
              <div className="research-empty-card"><h3>{t("research.no_sources")}</h3><p>{t("research.no_sources_body")}</p></div>
            ) : (
              <div className="research-card-list">
                {sources.map((source) => (
                  <article className="research-source-card" key={source.source_document_id}>
                    <div className="research-card-heading">
                      <div><p className="research-kicker">Union3 · arXiv</p><h3>2311.12098v4</h3></div>
                      <div className="research-badge-row">
                        <span className={`research-badge source-${source.lifecycle_status.toLowerCase()}`}>
                          {statusText(source.lifecycle_status)}
                        </span>
                        <span className="research-badge neutral">v{source.version}</span>
                      </div>
                    </div>
                    <p>{t("research.union3_source_scope")}</p>
                    <dl className="research-definition-grid">
                      <div><dt>{t("research.coverage")}</dt><dd>{statusText(source.coverage_status)}</dd></div>
                      <div><dt>{t("research.added")}</dt><dd>{formatTime(source.created_at)}</dd></div>
                    </dl>
                    {source.error && <p className="research-inline-error">{source.error}</p>}
                    <div className="research-card-actions">
                      <a href={source.source_url} target="_blank" rel="noreferrer">{t("research.open_source")}</a>
                      {source.lifecycle_status === "COMPLETED" && (
                        <button
                          className="btn-secondary"
                          onClick={() => { void toggleSourceDetails(source.source_document_id); }}
                        >
                          {expandedSourceId === source.source_document_id
                            ? t("research.hide_table9")
                            : t("research.read_table9")}
                        </button>
                      )}
                      {source.lifecycle_status.startsWith("FAILED") && readerEnabled && workspaceActive && (
                        <button className="btn-secondary" disabled={busy} onClick={() => { void retrySource(source.source_document_id); }}>
                          {t("research.retry_new_version")}
                        </button>
                      )}
                    </div>
                    {expandedSourceId === source.source_document_id && (
                      <div className="research-source-reader">
                        {sourceDetails[source.source_document_id]?.loading && (
                          <p>{t("research.loading_table9")}</p>
                        )}
                        {sourceDetails[source.source_document_id]?.error && (
                          <p className="research-inline-error" role="alert">
                            {sourceDetails[source.source_document_id].error}
                          </p>
                        )}
                        {sourceDetails[source.source_document_id]?.content
                          && sourceDetails[source.source_document_id]?.tables && (
                            <>
                              <div className="research-table-locator">
                                <strong>{sourceDetails[source.source_document_id].tables!.table_label}</strong>
                                <span>
                                  {t("research.section")} {sourceDetails[source.source_document_id].tables!.section_label}
                                  {" · "}{t("research.pdf_page")} {sourceDetails[source.source_document_id].tables!.pdf_page_label}
                                </span>
                                <span>{t("research.table_semantics")}: {t("research.profile_chi_square")}</span>
                              </div>
                              <div className="research-anchor-list">
                                {sourceDetails[source.source_document_id].content!.anchors.map((anchor) => (
                                  <figure key={anchor.anchor_id}>
                                    <figcaption>
                                      {String(anchor.locator.role || t("research.source_anchor"))}
                                      {" · "}{t("research.pdf_page")} {String(anchor.locator.pdf_page_label || "—")}
                                    </figcaption>
                                    <blockquote>{anchor.raw_text}</blockquote>
                                  </figure>
                                ))}
                              </div>
                              {sourceDetails[source.source_document_id].content!.limitations.length > 0 && (
                                <div className="research-reader-limitations">
                                  <strong>{t("research.limitations")}</strong>
                                  <ul>
                                    {sourceDetails[source.source_document_id].content!.limitations.map((item) => (
                                      <li key={item}>{item}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </>
                          )}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "claims" && (
          <div className="research-section-stack">
            <div className="research-section-heading">
              <div><h2>{t("research.claims_title")}</h2><p>{t("research.claims_body")}</p></div>
            </div>
            {!executionEnabled && (
              <div className="research-alert info" role="note">
                <strong>{t("research.execution_closed_title")}</strong>
                <span>{t("research.execution_closed_body")}</span>
              </div>
            )}
            {candidates.length === 0 ? (
              <div className="research-empty-card"><h3>{t("research.no_claims")}</h3><p>{t("research.no_claims_body")}</p></div>
            ) : (
              <div className="research-card-list">
                {candidates.map((record) => (
                  <article className="research-claim-card" key={record.candidate.candidate_id}>
                    <div className="research-card-heading">
                      <div><p className="research-kicker">{t("research.paper_candidate")}</p><h3>{record.candidate.claim_text}</h3></div>
                      <span className="research-badge warn">{t("research.review_required")}</span>
                    </div>
                    <div className="research-interval">
                      Ω<sub>m</sub> = {record.candidate.reported_value.central}
                      <sup>+{record.candidate.reported_value.plus}</sup>
                      <sub>−{record.candidate.reported_value.minus}</sub>
                    </div>
                    <p className="research-method-note">{t("research.frequentist_note")}</p>
                    <label className="research-workflow-select">
                      {t("research.workflow")}
                      <select value={WORKFLOW_KEY} disabled>
                        <option value={WORKFLOW_KEY}>{t("research.union3_workflow")}</option>
                      </select>
                    </label>
                    <p className="research-server-selection">{t("research.server_selects_inputs")}</p>
                    <button
                      className="btn-primary"
                      disabled={!executionEnabled || runningCandidateId !== null}
                      onClick={() => { void startRegisteredWorkflow(record); }}
                    >
                      {runningCandidateId === record.candidate.candidate_id
                        ? t("research.starting_run")
                        : t("research.start_run")}
                    </button>
                  </article>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "runs" && (
          <div className="research-section-stack">
            <div className="research-section-heading">
              <div><h2>{t("research.runs_title")}</h2><p>{t("research.runs_body")}</p></div>
            </div>
            {audits.length === 0 ? (
              <div className="research-empty-card"><h3>{t("research.no_runs")}</h3><p>{t("research.no_runs_body")}</p></div>
            ) : (
              <div className="research-card-list">
                {audits.map((audit) => (
                  <article className="research-run-card" key={audit.audit_id}>
                    <div className="research-card-heading">
                      <div><p className="research-kicker">{t("research.registered_run")}</p><h3>{audit.claim_text}</h3></div>
                      <div className="research-badge-row">
                        <span className={`research-badge run-${audit.lifecycle_status.toLowerCase()}`}>{statusText(audit.lifecycle_status)}</span>
                        <span className={`research-badge ${verdictClass(audit)}`}>{audit.scientific_verdict || t("research.pending")}</span>
                      </div>
                    </div>
                    <dl className="research-definition-grid">
                      <div><dt>{t("research.run_status")}</dt><dd>{statusText(audit.lifecycle_status)}</dd></div>
                      <div><dt>{t("research.scientific_status")}</dt><dd>{audit.scientific_verdict || t("research.pending")}</dd></div>
                      <div><dt>{t("research.review_status")}</dt><dd>{audit.review_status || t("research.pending")}</dd></div>
                      <div><dt>{t("research.started")}</dt><dd>{formatTime(audit.started_at || audit.created_at)}</dd></div>
                    </dl>
                    <p className="research-method-note">
                      {audit.scientific_verdict === "SUPPORTED"
                        ? t("research.supported_limit")
                        : t("research.withheld_limit")}
                    </p>
                    {(audit.can_cancel || audit.can_retry || audit.can_revise
                      || !["QUEUED", "RUNNING"].includes(audit.lifecycle_status)) && (
                      <div className="research-card-actions">
                        {audit.can_cancel && (
                          <button
                            className="btn-danger-sm"
                            disabled={auditActionId !== null}
                            onClick={() => { void cancelAudit(audit.audit_id); }}
                          >
                            {auditActionId === audit.audit_id
                              ? t("research.run_updating")
                              : t("research.cancel_run")}
                          </button>
                        )}
                        {audit.can_retry && (
                          <button
                            className="btn-primary"
                            disabled={auditActionId !== null}
                            onClick={() => { void retryAudit(audit.audit_id); }}
                          >
                            {auditActionId === audit.audit_id
                              ? t("research.run_updating")
                              : t("research.retry_run")}
                          </button>
                        )}
                        {audit.can_revise && workspaceActive && (
                          <button
                            className="btn-secondary"
                            disabled={auditActionId !== null}
                            onClick={() => { void reviseAudit(audit.audit_id); }}
                          >
                            {auditActionId === audit.audit_id
                              ? t("research.run_updating")
                              : t("research.revise_run")}
                          </button>
                        )}
                        {!["QUEUED", "RUNNING"].includes(audit.lifecycle_status) && (
                          <button
                            className="btn-danger-sm"
                            disabled={auditActionId !== null}
                            onClick={() => { void deleteAudit(audit.audit_id); }}
                          >
                            {t("research.delete_run")}
                          </button>
                        )}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "evidence" && (
          <div className="research-section-stack">
            <div className="research-section-heading">
              <div><h2>{t("research.evidence_title")}</h2><p>{t("research.evidence_body")}</p></div>
            </div>
            {!evidenceV2Enabled && (
              <div className="research-alert info" role="note">
                <strong>{t("research.evidence_closed_title")}</strong>
                <span>{t("research.evidence_closed_body")}</span>
              </div>
            )}
            {evidenceAudits.length === 0 ? (
              <div className="research-empty-card"><h3>{t("research.no_packs")}</h3><p>{t("research.no_packs_body")}</p></div>
            ) : (
              <div className="research-card-list">
                {evidenceAudits.map((audit) => {
                  const pack = audit.evidence_pack!;
                  return (
                    <article className="research-pack-card" key={pack.pack_id}>
                      <div className="research-card-heading">
                        <div><p className="research-kicker">{t("research.private_pack")}</p><h3>{audit.claim_text}</h3></div>
                        <span className="research-badge verified">{pack.status}</span>
                      </div>
                      <p>{t("research.pack_scope")}</p>
                      {evidenceV2Enabled && (
                        <div className="research-card-actions">
                          <button className="btn-primary" onClick={() => { void downloadPack(audit); }}>{t("research.download_pack")}</button>
                          <button className="btn-secondary" onClick={() => { void verifyPack(audit); }}>{t("research.verify_pack")}</button>
                        </div>
                      )}
                      {verification[pack.pack_id] && <p className="research-verification" role="status">{verification[pack.pack_id]}</p>}
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
