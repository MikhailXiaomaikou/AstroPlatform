import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  createResearchWorkspace,
  getRuntimeConfig,
  listResearchWorkspaces,
  type ResearchWorkspaceSummary,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../i18n";
import "./ResearchWorkspace.css";

type FeatureState = "loading" | "enabled" | "disabled" | "unreachable";

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

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

export default function ResearchPage() {
  const { user, loading: authLoading } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [featureState, setFeatureState] = useState<FeatureState>("loading");
  const [workspaces, setWorkspaces] = useState<ResearchWorkspaceSummary[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadWorkspaces = useCallback(async () => {
    const response = await listResearchWorkspaces();
    setWorkspaces(response.items);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((config) => {
        if (cancelled) return;
        setFeatureState(config.research_workspace_enabled === true ? "enabled" : "disabled");
      })
      .catch(() => {
        if (!cancelled) setFeatureState("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!user || featureState !== "enabled") return;
    let cancelled = false;
    void listResearchWorkspaces()
      .then((response) => {
        if (!cancelled) setWorkspaces(response.items);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(errorMessage(loadError, t("research.error.load_workspaces")));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [featureState, t, user]);

  async function submitWorkspace(event: React.FormEvent) {
    event.preventDefault();
    const cleanTitle = title.trim();
    if (!cleanTitle || busy) return;
    setBusy(true);
    setError(null);
    try {
      const workspace = await createResearchWorkspace({
        title: cleanTitle,
        description: description.trim(),
      });
      setTitle("");
      setDescription("");
      navigate(`/research/workspaces/${workspace.workspace_id}`);
    } catch (createError: unknown) {
      setError(errorMessage(createError, t("research.error.create_workspace")));
    } finally {
      setBusy(false);
    }
  }

  if (authLoading || featureState === "loading") {
    return <div className="research-page research-state">{t("research.loading")}</div>;
  }

  if (!user) {
    return (
      <div className="research-page research-state">
        <p className="research-eyebrow">{t("research.eyebrow")}</p>
        <h1>{t("research.title")}</h1>
        <p>{t("research.sign_in_body")}</p>
        <Link className="btn-primary" to="/auth">{t("nav.sign_in")}</Link>
      </div>
    );
  }

  if (featureState !== "enabled") {
    return (
      <div className="research-page research-state" data-testid="research-disabled">
        <p className="research-eyebrow">{t("research.eyebrow")}</p>
        <h1>{t("research.closed_title")}</h1>
        <p>{t("research.closed_body")}</p>
      </div>
    );
  }

  return (
    <div className="research-page">
      <header className="research-hero">
        <div>
          <p className="research-eyebrow">{t("research.eyebrow")}</p>
          <h1>{t("research.title")}</h1>
          <p>{t("research.subtitle")}</p>
        </div>
        <div className="research-safety-card" role="note">
          <strong>{t("research.safety_title")}</strong>
          <span>{t("research.safety_body")}</span>
        </div>
      </header>

      {error && <div className="research-alert error" role="alert">{error}</div>}

      <div className="research-index-grid">
        <section className="research-panel">
          <div className="research-panel-heading">
            <div>
              <p className="research-kicker">{t("research.private_label")}</p>
              <h2>{t("research.your_workspaces")}</h2>
            </div>
            <button
              className="btn-secondary"
              disabled={busy}
              onClick={() => {
                setError(null);
                void loadWorkspaces().catch((loadError: unknown) => {
                  setError(errorMessage(loadError, t("research.error.load_workspaces")));
                });
              }}
            >
              {t("research.refresh")}
            </button>
          </div>

          {workspaces.length === 0 ? (
            <div className="research-empty-card">
              <h3>{t("research.empty_title")}</h3>
              <p>{t("research.empty_body")}</p>
            </div>
          ) : (
            <div className="research-workspace-list">
              {workspaces.map((workspace) => (
                <Link
                  key={workspace.workspace_id}
                  className="research-workspace-card"
                  to={`/research/workspaces/${workspace.workspace_id}`}
                >
                  <div>
                    <h3>{workspace.title}</h3>
                    <span className={`research-badge status-${workspace.status.toLowerCase()}`}>
                      {workspace.status}
                    </span>
                  </div>
                  <p>{workspace.description || t("research.no_description")}</p>
                  <small>{t("research.updated")} {formatDate(workspace.updated_at)}</small>
                </Link>
              ))}
            </div>
          )}
        </section>

        <aside className="research-panel research-create-panel">
          <p className="research-kicker">{t("research.new_label")}</p>
          <h2>{t("research.create_title")}</h2>
          <p>{t("research.create_body")}</p>
          <form onSubmit={submitWorkspace}>
            <label>
              {t("research.workspace_name")}
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={160}
                required
                placeholder={t("research.workspace_name_placeholder")}
              />
            </label>
            <label>
              {t("research.workspace_description")}
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={20_000}
                rows={4}
                placeholder={t("research.workspace_description_placeholder")}
              />
            </label>
            <button className="btn-primary" disabled={!title.trim() || busy} type="submit">
              {busy ? t("research.creating") : t("research.create_action")}
            </button>
          </form>
        </aside>
      </div>
    </div>
  );
}
