import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import CommentSection from "./CommentSection";

/**
 * Home page — Journal Edition.
 * 1:1 port of journal-site/index.html #page-home:
 *   hero (eyebrow + title + italic subtitle + lead + 2 CTAs)
 *   5-stat strip
 *   "In this issue" TOC grid
 *   "Editorial principles" rail (4 numbered principles).
 */

// 2026-07-03: hand-maintained exact counts ("2,207 automated tests",
// "57 tools") kept drifting stale on a homepage whose subtitle is
// "refuses to fabricate". Values are now worded to stay true without
// per-release maintenance: floors ("1,000+") and design commitments
// ("0 tolerance") instead of live counts. `valueKey` entries are
// translated words; `value` entries are locale-neutral figures.
const STATS: { value?: string; valueKey?: string; labelKey: string }[] = [
  { valueKey: "home.stat.archives_v", labelKey: "home.stat.archives" },
  { value: "1",      labelKey: "home.stat.modules" },
  { valueKey: "home.stat.tools_v", labelKey: "home.stat.tools" },
  { value: "0",      labelKey: "home.stat.fabricated" },
  { value: "1,000+", labelKey: "home.stat.tests" },
];

interface TocEntry {
  catKey: string;
  title: string;
  body: string;
  meta: string;
  to?: string;
}

// Exported so tests can assert every clickable card targets a live route
// (M3 deleted /search, /pipeline, /adql, /workspace — see src/routes.ts).
// eslint-disable-next-line react-refresh/only-export-components -- data constant exported only for the route-liveness regression test; HMR trade-off accepted per eslint.config.js Q3 note
export const TOC: TocEntry[] = [
  {
    catKey: "home.cat.method",
    title: "DESI DR1 BAO with source-pinned vectors and covariance",
    body: "Run a numerical regression against the registered DESI bytes, then inspect why the fast sampler remains preliminary rather than publication-ready.",
    meta: "BAO likelihood · p. 1",
    to: "/chat",
  },
  // M3 note (2026-07-03): the "Bidirectional SAMP with TOPCAT and Aladin"
  // card was removed — it described the deleted data-browser page and no
  // SAMP surface remains in the UI. Cards must only claim live capabilities.
  {
    catKey: "home.cat.statistics",
    title: "Publication claims require independent-chain diagnostics",
    body: "Four independent chains, rank-normalized R̂ < 1.01 and bulk ESS ≥ 400 are blocking requirements; a flattened walker ensemble cannot pass.",
    meta: "Rigor · p. 13",
    to: "/chat",
  },
  {
    catKey: "home.cat.pipeline",
    title: "Long likelihood jobs survive page refreshes",
    body: "MCMC and robustness jobs run on a durable Celery queue with owner-scoped progress, cancellation, retry and recoverable result artifacts.",
    meta: "Research jobs · p. 19",
    to: "/chat",
  },
  {
    catKey: "home.cat.reproducibility",
    title: "Every tool return carries a reproducibility envelope",
    body: "run_id, tool_version, archive_version, query_hash, seed. Anyone can rerun your session.",
    meta: "Provenance · p. 23",
    to: "/chat",
  },
  {
    catKey: "home.cat.community",
    title: "Worked analyses for observational cosmology",
    body: "DESI BAO, calibrated Pantheon+ supernovae, Planck CMB paths, overlap audits and strict chain diagnostics — each labelled by its real evidence tier.",
    meta: "Workflow library · p. 27",
    to: "/chat",
  },
];

export default function LandingPage() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const eyebrowKey = "home.eyebrow";
  const titleKey = "home.title";

  return (
    <div className="journal-page journal-home">
      {/* Hero */}
      <section className="hero">
        <div className="hero-eyebrow">{t(eyebrowKey)}</div>
        <h1 className="hero-title">{t(titleKey)}</h1>
        <div className="hero-subtitle">{t("home.subtitle")}</div>
        <p className="hero-lead">{t("home.lead")}</p>
        <div className="hero-cta">
          <button className="btn-journal-primary" onClick={() => navigate("/chat")}>
            {t("home.cta1")}
          </button>
          <button className="btn-journal-ghost" onClick={() => navigate("/help")}>
            {t("home.cta2")}
          </button>
        </div>
      </section>

      {/* Stats strip */}
      <div className="stats-strip">
        {STATS.map((s) => (
          <div key={s.labelKey} className="stat">
            <div className="stat-n">{s.valueKey ? t(s.valueKey) : s.value}</div>
            <div className="stat-l">{t(s.labelKey)}</div>
          </div>
        ))}
      </div>

      {/* TOC */}
      <h2 className="section-head">{t("home.toc.head")}</h2>
      <div className="toc-grid">
        {TOC.map((card, i) => (
          <article
            key={i}
            className="toc-card"
            onClick={() => card.to && navigate(card.to)}
            style={{ cursor: card.to ? "pointer" : "default" }}
          >
            <div className="toc-cat">{t(card.catKey)}</div>
            <h3>{card.title}</h3>
            <p>{card.body}</p>
            <div className="toc-meta">{card.meta}</div>
          </article>
        ))}
      </div>

      {/* Editorial principles rail */}
      <div className="rail">
        <h2 className="section-head alt">{t("home.editorial.head")}</h2>
        <ol className="rail-list">
          <li>
            <strong>{t("home.ed.1")}</strong>{" "}{t("home.ed.1.body")}
          </li>
          <li>
            <strong>{t("home.ed.2")}</strong>{" "}{t("home.ed.2.body")}
          </li>
          <li>
            <strong>{t("home.ed.3")}</strong>{" "}{t("home.ed.3.body")}
          </li>
          <li>
            <strong>{t("home.ed.4")}</strong>{" "}{t("home.ed.4.body")}
          </li>
        </ol>
      </div>

      {/* Public comment section — open to all visitors, no login required */}
      <CommentSection />
    </div>
  );
}
