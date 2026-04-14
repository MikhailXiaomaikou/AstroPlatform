import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";

const FEATURES = [
  {
    icon: "\u2728",
    titleKey: "landing.feat_ai",
    titleFallback: "AI Research Assistant",
    descKey: "landing.feat_ai_desc",
    descFallback: "Chat with an AI that queries 21 databases, writes ADQL, builds pipelines, runs Python, and drafts papers.",
    link: "/chat",
    accent: "#8b5cf6",
  },
  {
    icon: "\uD83D\uDD2D",
    titleKey: "landing.feat_search",
    titleFallback: "Multi-Source Search",
    descKey: "landing.feat_search_desc",
    descFallback: "Search SDSS, Gaia, SIMBAD, and 18 more databases simultaneously with natural language or coordinates.",
    link: "/search",
    accent: "#2563eb",
  },
  {
    icon: "\u2699\uFE0F",
    titleKey: "landing.feat_pipeline",
    titleFallback: "Visual Pipeline Builder",
    descKey: "landing.feat_pipeline_desc",
    descFallback: "Build analysis workflows by connecting nodes — denoise, fit spectra, cross-match catalogs, and plot results.",
    link: "/pipeline",
    accent: "#10b981",
  },
  {
    icon: "\uD83D\uDCBE",
    titleKey: "landing.feat_adql",
    titleFallback: "ADQL Direct Query",
    descKey: "landing.feat_adql_desc",
    descFallback: "Write TAP queries with syntax highlighting against Gaia, SIMBAD, VizieR, and CADC services.",
    link: "/adql",
    accent: "#f59e0b",
  },
];

const STATS = [
  { value: "48", label: "AI Tools" },
  { value: "19+", label: "Databases" },
  { value: "35", label: "Pipeline Nodes" },
  { value: "4", label: "Languages" },
];

export default function LandingPage() {
  const navigate = useNavigate();
  const { t } = useI18n();

  return (
    <div className="landing-page">
      {/* Hero */}
      <section className="landing-hero">
        <div className="landing-hero-stars" />
        <h1 className="landing-title">Standard Astro</h1>
        <p className="landing-subtitle">
          {t("landing.subtitle") !== "landing.subtitle"
            ? t("landing.subtitle")
            : "AI-powered research platform \u2014 query 19 databases, analyze spectra, build pipelines, and draft papers with one conversation."}
        </p>
        <div className="landing-cta">
          <button className="btn-landing btn-landing-ai" onClick={() => navigate("/chat")}>
            Start with AI Assistant
          </button>
          <button className="btn-primary btn-landing" onClick={() => navigate("/search")}>
            Browse Data
          </button>
        </div>
      </section>

      {/* Features */}
      <section className="landing-features">
        {FEATURES.map((f) => (
          <div
            key={f.link}
            className="landing-feature-card"
            onClick={() => navigate(f.link)}
            style={{ borderTopColor: f.accent }}
          >
            <span className="landing-feature-icon">{f.icon}</span>
            <h3>{t(f.titleKey) !== f.titleKey ? t(f.titleKey) : f.titleFallback}</h3>
            <p>{t(f.descKey) !== f.descKey ? t(f.descKey) : f.descFallback}</p>
          </div>
        ))}
      </section>

      {/* Stats */}
      <section className="landing-stats">
        {STATS.map((s) => (
          <div key={s.label} className="landing-stat">
            <span className="landing-stat-value">{s.value}</span>
            <span className="landing-stat-label">{s.label}</span>
          </div>
        ))}
      </section>
    </div>
  );
}
