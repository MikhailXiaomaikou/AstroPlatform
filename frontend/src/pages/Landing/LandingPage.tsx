import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { getBackendConfig } from "../../api/config";
import CommentSection from "./CommentSection";

/**
 * Home page — Journal Edition.
 * 1:1 port of journal-site/index.html #page-home:
 *   hero (eyebrow + title + italic subtitle + lead + 2 CTAs)
 *   5-stat strip
 *   "In this issue" TOC grid (6 cards)
 *   "Editorial principles" rail (4 numbered principles).
 */

const STATS: { value: string; labelKey: string }[] = [
  { value: "23",  labelKey: "home.stat.archives" },
  { value: "35",  labelKey: "home.stat.nodes" },
  { value: "52",  labelKey: "home.stat.tools" },
  { value: "0",   labelKey: "home.stat.fabricated" },
  { value: "859", labelKey: "home.stat.tests" },
];

interface TocEntry {
  catKey: string;
  title: string;
  body: string;
  meta: string;
  to?: string;
}

const TOC: TocEntry[] = [
  {
    catKey: "home.cat.method",
    title: "A turnoff-based age for NGC 752 from Gaia DR3",
    body: "Two-stage PARSEC grid with BP−RP binning recovers 1.56 Gyr, consistent with Twarog+97 and Agüeros+18.",
    meta: "Open-cluster photometry · p. 1",
    to: "/chat",
  },
  {
    catKey: "home.cat.instrument",
    title: "Bidirectional SAMP with TOPCAT and Aladin",
    body: "Highlight a row in the data browser; TOPCAT scrolls to it. Point at a sky position in Aladin; chat picks up the coordinates.",
    meta: "VO interoperability · p. 7",
    to: "/search",
  },
  {
    catKey: "home.cat.statistics",
    title: "Every MCMC report carries ESS, R̂, HDI, WAIC and LOO",
    body: "arviz diagnostics are blocking. Chains with ESS < 400 or R̂ > 1.05 are labelled insufficient before they can cite.",
    meta: "Rigor · p. 13",
    to: "/pipeline",
  },
  {
    catKey: "home.cat.pipeline",
    title: "Async TAP for heavy cone searches",
    body: "A 45 s sync fallback triggers pyvo launch_job_async; the job URL is returned so a user can poll it.",
    meta: "Gaia DR3 · p. 19",
    to: "/adql",
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
    title: "Templates for five canonical analyses",
    body: "HR + isochrone, SN Ia peak magnitude, QSO single-epoch BH mass, photo-z catalogue, variable-star folding.",
    meta: "Workflow library · p. 27",
    to: "/workspace",
  },
];

export default function LandingPage() {
  const navigate = useNavigate();
  const { t } = useI18n();
  // M0 Commit 6 (2026-05-18): Switch the hero copy based on backend focus. cosmology is the default;
  // solar_system uses the .solar_system suffix keys. Unknown focus / fetch failure -> cosmology default.
  const [backendFocus, setBackendFocus] = useState<string>("cosmology");
  useEffect(() => {
    getBackendConfig()
      .then((cfg) => setBackendFocus(cfg.focus || "cosmology"))
      .catch(() => {});
  }, []);
  const eyebrowKey =
    backendFocus === "solar_system" ? "home.eyebrow.solar_system" : "home.eyebrow";
  const titleKey =
    backendFocus === "solar_system" ? "home.title.solar_system" : "home.title";

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
            <div className="stat-n">{s.value}</div>
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
