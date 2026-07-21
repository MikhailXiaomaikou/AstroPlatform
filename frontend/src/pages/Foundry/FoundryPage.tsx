import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  getAdminFoundryCandidate,
  getFoundryCandidate,
  getRuntimeConfig,
  listAdminFoundryRequests,
  listCapabilityRequests,
  listFoundryDemoRuns,
  registerAdminFoundryCandidate,
  reviewAdminFoundryCandidate,
  revokeAdminFoundryCandidate,
  suspendAdminFoundryCandidate,
  triageAdminFoundryRequest,
  validateAdminFoundryCandidate,
  type CapabilityRequestSummary,
  type FoundryCandidateSummary,
  type FoundryCandidateVersion,
  type FoundryDemoRun,
  type RuntimeConfig,
  type WorkflowRiskLevel,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../i18n";
import { localizedApiError } from "../../utils/apiErrors";
import "./FoundryPage.css";

type FeatureState = "loading" | "enabled" | "disabled" | "unreachable";
type AdminAccess = "loading" | "allowed" | "denied" | "error";
type GenerationRoute = "COMPOSITION" | "DATA_ADAPTER" | "SCIENCE_CODE";
type ReviewScope = "ENGINEERING" | "SCIENTIFIC";

interface CandidateView {
  candidate: FoundryCandidateSummary;
  demos: FoundryDemoRun[];
}

function responseStatus(error: unknown): number | undefined {
  return (error as { response?: { status?: number } })?.response?.status;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function displayStatus(value: string): string {
  return value.replaceAll("_", " ");
}

function versionBinding(version: FoundryCandidateVersion | null): {
  candidateVersionId: string;
  versionHash: string;
} | null {
  if (!version) return null;
  const candidateVersionId = version.id;
  const versionHash = version.version_hash;
  return candidateVersionId && versionHash ? { candidateVersionId, versionHash } : null;
}

function DemoCard({ demo }: { demo: FoundryDemoRun }) {
  const { t } = useI18n();
  return (
    <article className="foundry-demo-card">
      <div className="foundry-card-heading">
        <div>
          <p className="foundry-kicker">{t("foundry.demo_run")}</p>
          <h3>{demo.demo_run_id}</h3>
        </div>
        <span className={`foundry-badge demo-${demo.status.toLowerCase()}`}>
          {displayStatus(demo.status)}
        </span>
      </div>
      <div className="foundry-nonformal" role="note">
        <strong>{t("foundry.non_formal_badge")}</strong>
        <span>{t("foundry.non_formal_warning")}</span>
      </div>
      <dl className="foundry-definition-grid">
        <div><dt>{t("foundry.evidence_class")}</dt><dd>{demo.evidence_class}</dd></div>
        <div><dt>{t("foundry.version")}</dt><dd>{String(demo.candidate_version)}</dd></div>
        <div><dt>{t("foundry.publication_ready")}</dt><dd>{demo.publication_ready ? t("foundry.yes") : t("foundry.no")}</dd></div>
        <div><dt>{t("foundry.claim_eligible")}</dt><dd>{demo.claim_eligible ? t("foundry.yes") : t("foundry.no")}</dd></div>
        <div><dt>{t("foundry.started")}</dt><dd>{formatTime(demo.started_at)}</dd></div>
        <div><dt>{t("foundry.completed")}</dt><dd>{formatTime(demo.completed_at)}</dd></div>
      </dl>
      {demo.limitations.length > 0 && (
        <div className="foundry-limitations">
          <strong>{t("foundry.limitations")}</strong>
          <ul>{demo.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      )}
      <details>
        <summary>{t("foundry.validation_summary")}</summary>
        <pre>{typeof demo.validation_summary === "string"
          ? demo.validation_summary
          : JSON.stringify(demo.validation_summary, null, 2)}</pre>
      </details>
    </article>
  );
}

function CandidateCard({ view }: { view: CandidateView }) {
  const { t } = useI18n();
  const { candidate, demos } = view;
  const versions = candidate.versions
    ?? (candidate.current_version ? [candidate.current_version] : []);
  return (
    <article className="foundry-candidate-card">
      <div className="foundry-card-heading">
        <div>
          <p className="foundry-kicker">{t("foundry.candidate_label")}</p>
          <h2>{candidate.gap_code}</h2>
        </div>
        <div className="foundry-badge-row">
          <span className="foundry-badge candidate">{t("foundry.candidate_badge")}</span>
          <span className={`foundry-badge status-${candidate.status.toLowerCase()}`}>
            {displayStatus(candidate.status)}
          </span>
        </div>
      </div>
      <p>{t("foundry.candidate_boundary")}</p>
      <dl className="foundry-definition-grid">
        <div><dt>{t("foundry.route")}</dt><dd>{displayStatus(candidate.generation_route || "UNTRIAGED")}</dd></div>
        <div><dt>{t("foundry.risk")}</dt><dd>{candidate.risk_level || "—"}</dd></div>
        <div><dt>{t("foundry.fingerprint")}</dt><dd><code>{candidate.gap_fingerprint}</code></dd></div>
        <div><dt>{t("foundry.updated")}</dt><dd>{formatTime(candidate.updated_at)}</dd></div>
      </dl>

      <section className="foundry-subsection">
        <h3>{t("foundry.versions")}</h3>
        {versions.length === 0 ? (
          <p>{t("foundry.no_versions")}</p>
        ) : (
          <div className="foundry-version-list">
            {versions.map((version) => (
              <div key={version.id}>
                <strong>v{version.version_number}</strong>
                <span>{version.workflow_id}</span>
                <code>{version.version_hash}</code>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="foundry-subsection">
        <h3>{t("foundry.demo_runs")}</h3>
        {demos.length === 0 ? <p>{t("foundry.no_demos")}</p> : demos.map((demo) => (
          <DemoCard key={demo.demo_run_id} demo={demo} />
        ))}
      </section>
    </article>
  );
}

export default function FoundryPage() {
  const { user, loading: authLoading } = useAuth();
  const { t } = useI18n();
  const [featureState, setFeatureState] = useState<FeatureState>("loading");
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [requests, setRequests] = useState<CapabilityRequestSummary[]>([]);
  const [candidateViews, setCandidateViews] = useState<Record<string, CandidateView>>({});
  const [adminAccess, setAdminAccess] = useState<AdminAccess>("loading");
  const [adminRequests, setAdminRequests] = useState<CapabilityRequestSummary[]>([]);
  const [adminCandidates, setAdminCandidates] = useState<Record<string, FoundryCandidateSummary>>({});
  const [routes, setRoutes] = useState<Record<string, GenerationRoute>>({});
  const [risks, setRisks] = useState<Record<string, Exclude<WorkflowRiskLevel, "R4">>>({});
  const [comments, setComments] = useState<Record<string, string>>({});
  const [reviewScopes, setReviewScopes] = useState<Record<string, ReviewScope>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadUserCatalog = useCallback(async () => {
    const response = await listCapabilityRequests();
    setRequests(response.items);
    const ids = [...new Set(response.items.flatMap((item) => (
      item.candidate_id ? [item.candidate_id] : []
    )))];
    const loaded = await Promise.all(ids.map(async (candidateId) => {
      const [candidate, demos] = await Promise.all([
        getFoundryCandidate(candidateId),
        listFoundryDemoRuns(candidateId),
      ]);
      return [candidateId, { candidate, demos: demos.items }] as const;
    }));
    setCandidateViews(Object.fromEntries(loaded));
  }, []);

  const loadAdminConsole = useCallback(async () => {
    try {
      const response = await listAdminFoundryRequests();
      setAdminRequests(response.items);
      setAdminAccess("allowed");
      const ids = [...new Set(response.items.flatMap((item) => (
        item.candidate_id ? [item.candidate_id] : []
      )))];
      const loaded = await Promise.all(ids.map(async (candidateId) => (
        [candidateId, await getAdminFoundryCandidate(candidateId)] as const
      )));
      setAdminCandidates(Object.fromEntries(loaded));
    } catch (loadError: unknown) {
      if ([401, 403, 404].includes(responseStatus(loadError) ?? 0)) {
        setAdminAccess("denied");
        setAdminRequests([]);
        setAdminCandidates({});
        return;
      }
      setAdminAccess("error");
      throw loadError;
    }
  }, []);

  const refresh = useCallback(async () => {
    setError(null);
    await loadUserCatalog();
    await loadAdminConsole();
  }, [loadAdminConsole, loadUserCatalog]);

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((config) => {
        if (!cancelled) {
          setConfig(config);
          setFeatureState(config.foundry_candidate_catalog_enabled === true ? "enabled" : "disabled");
        }
      })
      .catch(() => {
        if (!cancelled) setFeatureState("unreachable");
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!user || featureState !== "enabled") return;
    let cancelled = false;
    void refresh().catch((loadError: unknown) => {
      if (!cancelled) setError(localizedApiError(loadError, t, "foundry.error.load"));
    });
    return () => { cancelled = true; };
  }, [featureState, refresh, t, user]);

  const uniqueCandidateViews = useMemo(() => Object.values(candidateViews), [candidateViews]);

  async function runAdminAction(id: string, action: () => Promise<unknown>, successKey: string) {
    if (busyId) return;
    setBusyId(id);
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh();
      setNotice(t(successKey));
    } catch (actionError: unknown) {
      setError(localizedApiError(actionError, t, "foundry.error.action"));
    } finally {
      setBusyId(null);
    }
  }

  function bindingFor(candidateId: string) {
    return versionBinding(adminCandidates[candidateId]?.current_version ?? null);
  }

  if (authLoading || featureState === "loading") {
    return <div className="foundry-page foundry-state">{t("foundry.loading")}</div>;
  }
  if (!user) {
    return (
      <div className="foundry-page foundry-state">
        <h1>{t("foundry.title")}</h1>
        <p>{t("foundry.sign_in")}</p>
        <Link className="btn-primary" to="/auth">{t("nav.sign_in")}</Link>
      </div>
    );
  }
  if (featureState !== "enabled") {
    return (
      <div className="foundry-page foundry-state" data-testid="foundry-disabled">
        <h1>{t("foundry.closed_title")}</h1>
        <p>{t("foundry.closed_body")}</p>
        <Link to="/research">{t("foundry.back_research")}</Link>
      </div>
    );
  }

  return (
    <div className="foundry-page">
      <header className="foundry-hero">
        <div>
          <p className="foundry-kicker">{t("foundry.eyebrow")}</p>
          <h1>{t("foundry.title")}</h1>
          <p>{t("foundry.subtitle")}</p>
        </div>
        <div className="foundry-nonformal hero" role="note">
          <strong>{t("foundry.non_formal_badge")}</strong>
          <span>{t("foundry.non_formal_warning")}</span>
        </div>
      </header>
      <div className="foundry-toolbar">
        <Link to="/research">← {t("foundry.back_research")}</Link>
        <button className="btn-secondary" disabled={busyId !== null} onClick={() => {
          void refresh().catch((loadError: unknown) => {
            setError(localizedApiError(loadError, t, "foundry.error.load"));
          });
        }}>{t("research.refresh")}</button>
      </div>
      {error && <div className="research-alert error" role="alert">{error}</div>}
      {notice && <div className="research-alert success" role="status">{notice}</div>}

      <section className="foundry-section" aria-labelledby="foundry-requests-title">
        <div className="foundry-section-heading">
          <div><h2 id="foundry-requests-title">{t("foundry.your_requests")}</h2><p>{t("foundry.requests_body")}</p></div>
        </div>
        {requests.length === 0 ? <p className="foundry-empty">{t("foundry.no_requests")}</p> : (
          <div className="foundry-request-list">
            {requests.map((request) => (
              <article key={request.id}>
                <div>
                  <strong>{request.gap_fingerprint}</strong>
                  <span>{displayStatus(request.status)}</span>
                </div>
                <small>{formatTime(request.updated_at ?? request.created_at)}</small>
                {request.candidate_id && <a href={`#candidate-${request.candidate_id}`}>{t("foundry.open_candidate")}</a>}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="foundry-section" aria-labelledby="foundry-candidates-title">
        <div className="foundry-section-heading">
          <div><h2 id="foundry-candidates-title">{t("foundry.candidates")}</h2><p>{t("foundry.candidates_body")}</p></div>
        </div>
        {uniqueCandidateViews.length === 0 ? <p className="foundry-empty">{t("foundry.no_candidates")}</p> : (
          <div className="foundry-candidate-list">
            {uniqueCandidateViews.map((view) => (
              <div id={`candidate-${view.candidate.id}`} key={view.candidate.id}><CandidateCard view={view} /></div>
            ))}
          </div>
        )}
      </section>

      {adminAccess === "allowed" && (
        <section className="foundry-section foundry-admin" aria-labelledby="foundry-admin-title">
          <div className="foundry-section-heading">
            <div><p className="foundry-kicker">{t("foundry.admin_label")}</p><h2 id="foundry-admin-title">{t("foundry.admin_title")}</h2><p>{t("foundry.admin_body")}</p></div>
          </div>
          {adminRequests.length === 0 ? <p className="foundry-empty">{t("foundry.admin_empty")}</p> : (
            <div className="foundry-admin-list">
              {adminRequests.map((request) => {
                const candidate = request.candidate_id ? adminCandidates[request.candidate_id] : undefined;
                const binding = request.candidate_id ? bindingFor(request.candidate_id) : null;
                const comment = comments[request.id] ?? "";
                const reviewScope = reviewScopes[request.id]
                  ?? (candidate?.risk_level === "R0" || candidate?.risk_level === "R1"
                    ? "ENGINEERING"
                    : "SCIENTIFIC");
                const needsTriage = !request.candidate_id || Boolean(candidate && (
                  candidate.status === "DRAFT"
                  || !candidate.generation_route
                  || !candidate.risk_level
                  || !candidate.current_version
                ));
                return (
                  <article key={request.id}>
                    <div className="foundry-card-heading">
                      <div><p className="foundry-kicker">{request.gap_fingerprint}</p><h3>{displayStatus(request.status)}</h3></div>
                      {candidate?.risk_level && <span className="foundry-badge candidate">{candidate.risk_level}</span>}
                    </div>
                    {needsTriage ? (
                      <div className="foundry-admin-controls">
                        <label>{t("foundry.route")}
                          <select value={routes[request.id] ?? "COMPOSITION"} onChange={(event) => setRoutes((current) => ({ ...current, [request.id]: event.target.value as GenerationRoute }))}>
                            <option value="COMPOSITION">COMPOSITION</option>
                            <option value="DATA_ADAPTER">DATA ADAPTER</option>
                            <option value="SCIENCE_CODE">SCIENCE CODE</option>
                          </select>
                        </label>
                        <label>{t("foundry.risk")}
                          <select value={risks[request.id] ?? "R1"} onChange={(event) => setRisks((current) => ({ ...current, [request.id]: event.target.value as Exclude<WorkflowRiskLevel, "R4"> }))}>
                            <option value="R0">R0</option><option value="R1">R1</option><option value="R2">R2</option><option value="R3">R3</option>
                          </select>
                        </label>
                        <button className="btn-primary" disabled={busyId !== null} onClick={() => {
                          void runAdminAction(request.id, () => triageAdminFoundryRequest(request.id, {
                            generation_route: routes[request.id] ?? "COMPOSITION",
                            risk_level: risks[request.id] ?? "R1",
                          }), "foundry.action_triaged");
                        }}>{t("foundry.triage")}</button>
                      </div>
                    ) : candidate ? (
                      <>
                        <dl className="foundry-definition-grid">
                          <div><dt>{t("foundry.candidate_label")}</dt><dd>{candidate.id}</dd></div>
                          <div><dt>{t("foundry.version")}</dt><dd>{candidate.current_version?.version_number ?? "—"}</dd></div>
                        </dl>
                        {!binding && <p className="foundry-binding-warning">{t("foundry.binding_missing")}</p>}
                        <label className="foundry-comment">{t("foundry.review_comment")}
                          <textarea rows={3} value={comment} onChange={(event) => setComments((current) => ({ ...current, [request.id]: event.target.value }))} />
                        </label>
                        <label className="foundry-review-scope">{t("foundry.review_scope")}
                          <select value={reviewScope} onChange={(event) => setReviewScopes((current) => ({ ...current, [request.id]: event.target.value as ReviewScope }))}>
                            <option value="ENGINEERING">{t("foundry.review_scope_engineering")}</option>
                            <option value="SCIENTIFIC">{t("foundry.review_scope_scientific")}</option>
                          </select>
                        </label>
                        <p className="foundry-review-boundary">{t("foundry.review_separation")}</p>
                        <div className="foundry-action-row">
                          <button className="btn-secondary" disabled={!binding || !config?.foundry_auto_demo_enabled || busyId !== null} onClick={() => {
                            if (!binding || !request.candidate_id) return;
                            void runAdminAction(request.id, () => validateAdminFoundryCandidate(request.candidate_id!, {
                              candidate_version_id: binding.candidateVersionId,
                              candidate_version_hash: binding.versionHash,
                            }), "foundry.action_validation_started");
                          }}>{t("foundry.validate")}</button>
                          <button className="btn-primary" disabled={!binding || !comment.trim() || busyId !== null} onClick={() => {
                            if (!binding || !request.candidate_id) return;
                            void runAdminAction(request.id, () => reviewAdminFoundryCandidate(request.candidate_id!, {
                              candidate_version_id: binding.candidateVersionId,
                              candidate_version_hash: binding.versionHash,
                              review_scope: reviewScope,
                              decision: "APPROVED",
                              comment,
                            }), "foundry.action_reviewed");
                          }}>{t("foundry.approve")}</button>
                          <button className="btn-secondary" disabled={!binding || !comment.trim() || busyId !== null} onClick={() => {
                            if (!binding || !request.candidate_id) return;
                            void runAdminAction(request.id, () => reviewAdminFoundryCandidate(request.candidate_id!, {
                              candidate_version_id: binding.candidateVersionId,
                              candidate_version_hash: binding.versionHash,
                              review_scope: reviewScope,
                              decision: "CHANGES_REQUESTED",
                              comment,
                            }), "foundry.action_reviewed");
                          }}>{t("foundry.request_changes")}</button>
                          <button className="btn-danger-sm" disabled={!binding || !comment.trim() || busyId !== null} onClick={() => {
                            if (!binding || !request.candidate_id) return;
                            void runAdminAction(request.id, () => reviewAdminFoundryCandidate(request.candidate_id!, {
                              candidate_version_id: binding.candidateVersionId,
                              candidate_version_hash: binding.versionHash,
                              review_scope: reviewScope,
                              decision: "REJECTED",
                              comment,
                            }), "foundry.action_reviewed");
                          }}>{t("foundry.reject")}</button>
                          <button className="btn-primary" disabled={!binding || !config?.foundry_registration_enabled || candidate.status !== "APPROVED" || busyId !== null} onClick={() => {
                            if (!binding || !request.candidate_id) return;
                            void runAdminAction(request.id, () => registerAdminFoundryCandidate(request.candidate_id!, binding.candidateVersionId, binding.versionHash), "foundry.action_registered");
                          }}>{t("foundry.register")}</button>
                          <button className="btn-secondary" disabled={!comment.trim() || busyId !== null} onClick={() => {
                            if (!request.candidate_id) return;
                            void runAdminAction(request.id, () => suspendAdminFoundryCandidate(request.candidate_id!, comment), "foundry.action_suspended");
                          }}>{t("foundry.suspend")}</button>
                          <button className="btn-danger-sm" disabled={!comment.trim() || busyId !== null} onClick={() => {
                            if (!request.candidate_id) return;
                            void runAdminAction(request.id, () => revokeAdminFoundryCandidate(request.candidate_id!, comment), "foundry.action_revoked");
                          }}>{t("foundry.revoke")}</button>
                        </div>
                      </>
                    ) : <p>{t("foundry.loading_candidate")}</p>}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
