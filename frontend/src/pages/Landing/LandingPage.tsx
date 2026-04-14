import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";

const FEATURES = [
  { icon: "\u2728", titleKey: "landing.feat_ai", descKey: "landing.feat_ai_desc", link: "/chat", accent: "#8b5cf6" },
  { icon: "\uD83D\uDD2D", titleKey: "landing.feat_search", descKey: "landing.feat_search_desc", link: "/search", accent: "#2563eb" },
  { icon: "\u2699\uFE0F", titleKey: "landing.feat_pipeline", descKey: "landing.feat_pipeline_desc", link: "/pipeline", accent: "#10b981" },
  { icon: "\uD83D\uDCBE", titleKey: "landing.feat_adql", descKey: "landing.feat_adql_desc", link: "/adql", accent: "#f59e0b" },
];

const STATS = [
  { value: "48", labelKey: "landing.stat_ai_tools" },
  { value: "19+", labelKey: "landing.stat_databases" },
  { value: "35", labelKey: "landing.stat_pipeline_nodes" },
  { value: "4", labelKey: "landing.stat_languages" },
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
        <p className="landing-subtitle">{t("landing.subtitle")}</p>
        <div className="landing-cta">
          <button className="btn-landing btn-landing-ai" onClick={() => navigate("/chat")}>
            {t("landing.cta_ai")}
          </button>
          <button className="btn-primary btn-landing" onClick={() => navigate("/search")}>
            {t("landing.cta_browse")}
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
            <h3>{t(f.titleKey)}</h3>
            <p>{t(f.descKey)}</p>
          </div>
        ))}
      </section>

      {/* Stats */}
      <section className="landing-stats">
        {STATS.map((s) => (
          <div key={s.labelKey} className="landing-stat">
            <span className="landing-stat-value">{s.value}</span>
            <span className="landing-stat-label">{t(s.labelKey)}</span>
          </div>
        ))}
      </section>
    </div>
  );
}
