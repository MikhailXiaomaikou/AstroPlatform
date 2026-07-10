import { useState, useMemo } from "react";
import { GLOSSARY } from "../../data/glossary";
import { useI18n } from "../../i18n";
import type { Lang } from "../../i18n";

/* ── tiny helpers ── */

function tx(en: string, zh: string, lang: Lang): string {
  return lang === "zh" ? zh : en;
}

/* ── Tab keys ── */

type Tab = "quickstart" | "tutorials" | "glossary" | "faq" | "shortcuts";

const TABS: { key: Tab; en: string; zh: string }[] = [
  { key: "quickstart", en: "Quick Start", zh: "快速入门" },
  { key: "tutorials", en: "Research Tutorials", zh: "研究案例" },
  { key: "glossary", en: "Glossary", zh: "术语表" },
  { key: "faq", en: "FAQ", zh: "常见问题" },
  { key: "shortcuts", en: "Keyboard Shortcuts", zh: "快捷键" },
];

/* ── FAQ data ── */

interface FaqItem {
  q: { en: string; zh: string };
  a: { en: string; zh: string };
}

const FAQ_DATA: FaqItem[] = [
  {
    q: {
      en: "What databases does Standard Astro search?",
      zh: "Standard Astro 能搜索哪些数据库？",
    },
    a: {
      en: "During the provenance-v2 rollout, the active searchable sources are SIMBAD, Gaia, VizieR, NED, 2MASS, and ALMA Science Archive observation metadata. Other connectors remain visible but are marked under maintenance until their archive-version provenance is upgraded. ALMA currently provides observation metadata; derived line luminosities and FWHM values need a cited line-measurement table or literature search.",
      zh: "在 provenance-v2 推出期间，当前可用的数据源是 SIMBAD、Gaia、VizieR、NED、2MASS，以及 ALMA Science Archive 的观测元数据。其他 connector 仍会显示，但会标记为维护中，直到补齐 archive-version provenance。ALMA 当前提供观测元数据；派生的谱线光度和 FWHM 需要有引用支撑的谱线测量表或文献检索。",
    },
  },
  {
    q: {
      en: "Do I need to know Python to use the platform?",
      zh: "使用平台需要会 Python 吗？",
    },
    a: {
      en: "No. The AI Assistant can write and run Python code for you. Just describe what you want in plain language. If you do know Python, you can also write code directly in the sandbox.",
      zh: "不需要。AI 助手可以为您编写和运行 Python 代码。您只需用自然语言描述需求。如果您会 Python，也可以直接在沙盒中编写代码。",
    },
  },
  {
    q: {
      en: "How do I export my results for a paper?",
      zh: "如何将结果导出用于论文？",
    },
    a: {
      en: "You can export search results as CSV or VOTable, chat sessions as Jupyter Notebooks or Markdown, and plots as publication-ready SVG/PNG figures with customizable fonts and color schemes.",
      zh: "您可以将搜索结果导出为 CSV 或 VOTable，聊天会话导出为 Jupyter Notebook 或 Markdown，图表导出为可用于发表的 SVG/PNG 图像，并支持自定义字体和配色。",
    },
  },
  {
    q: {
      en: "How do I search for objects?",
      zh: "如何搜索天体？",
    },
    a: {
      en: "Ask the AI Assistant in plain language. It resolves object names (e.g. \"M31\") or coordinates across the connected databases and returns provenance-tagged result cards that you can download as CSV or VOTable.",
      zh: "用自然语言问 AI 助手即可。它可以按天体名称（如 \"M31\"）或坐标在已接入的数据库中检索，返回带溯源标记的结果卡片，并可下载为 CSV 或 VOTable。",
    },
  },
  {
    q: {
      en: "Can the AI write code for me?",
      zh: "AI 能为我编写代码吗？",
    },
    a: {
      en: "Yes. The AI Assistant runs Python in a sandboxed environment with NumPy, SciPy, Astropy, and plotting libraries pre-installed. It can query databases, analyze spectra, fit models, and generate publication-quality plots — all from a natural language request.",
      zh: "可以。AI 助手在预装了 NumPy、SciPy、Astropy 和绘图库的沙盒环境中运行 Python。它可以查询数据库、分析光谱、拟合模型和生成发表级图表——全部通过自然语言请求完成。",
    },
  },
  {
    q: {
      en: "How do I upload my own FITS files?",
      zh: "如何上传自己的 FITS 文件？",
    },
    a: {
      en: "Drag and drop a FITS or CSV file into the AI chat. Uploaded files can be analyzed by the AI in the sandboxed Python environment.",
      zh: "将 FITS 或 CSV 文件拖放到 AI 聊天窗口中。上传的文件可以由 AI 在沙盒 Python 环境中分析。",
    },
  },
  {
    q: {
      en: "What is ADQL and when should I use it?",
      zh: "什么是 ADQL，什么时候应该使用它？",
    },
    a: {
      en: "ADQL (Astronomical Data Query Language) is an SQL-like language for querying astronomical databases via TAP services. Use it when you need complex queries with joins, filters, or aggregations that go beyond simple name/coordinate searches — just ask the AI Assistant to write and run the query for you.",
      zh: "ADQL（天文数据查询语言）是一种类似 SQL 的语言，用于通过 TAP 服务查询天文数据库。当您需要超越简单名称/坐标搜索的复杂查询（如联接、筛选或聚合）时使用——直接让 AI 助手为您编写并运行查询即可。",
    },
  },
  {
    q: {
      en: "Can I collaborate with my team?",
      zh: "可以与团队协作吗？",
    },
    a: {
      en: "Yes. The Team page lets you create shared workspaces where members can share queries, pipeline templates, and saved results. Each member can have different permission levels.",
      zh: "可以。团队页面允许您创建共享工作区，成员可以共享查询、流水线模板和保存的结果。每个成员可以有不同的权限级别。",
    },
  },
];

/* ── Tutorial data ── */

interface Tutorial {
  title: { en: string; zh: string };
  desc: { en: string; zh: string };
  outcome: { en: string; zh: string };
}

const TUTORIALS: Tutorial[] = [
  {
    title: { en: "HR Diagram of a Star Cluster", zh: "星团的赫罗图" },
    desc: {
      en: "Query Gaia DR3 for a nearby open cluster, filter members by parallax and proper motion, and construct a color-magnitude diagram.",
      zh: "从 Gaia DR3 查询附近疏散星团，通过视差和自行筛选成员星，构建颜色-星等图。",
    },
    outcome: {
      en: "You'll produce a publication-ready CMD showing the main sequence, turnoff point, and any giant branch stars.",
      zh: "您将生成可用于发表的 CMD，显示主序、拐点和巨星支。",
    },
  },
  {
    title: { en: "Variable Star Light Curve Analysis", zh: "变星光变曲线分析" },
    desc: {
      en: "Retrieve time-series photometry, apply the Lomb-Scargle periodogram to find the period, and phase-fold the light curve.",
      zh: "获取时间序列测光数据，使用 Lomb-Scargle 周期图找到周期，并将光变曲线相位折叠。",
    },
    outcome: {
      en: "You'll detect periodic signals, classify the variable type (Cepheid, RR Lyrae, eclipsing binary), and measure the amplitude.",
      zh: "您将检测周期信号，分类变星类型（造父变星、天琴座 RR 型、食双星），并测量振幅。",
    },
  },
  {
    title: { en: "Galaxy Spectral Classification (BPT)", zh: "星系光谱分类（BPT 图）" },
    desc: {
      en: "Cross-match SDSS spectroscopic galaxies, measure emission line ratios, and plot the BPT diagram.",
      zh: "交叉匹配 SDSS 光谱星系，测量发射线比值，绘制 BPT 图。",
    },
    outcome: {
      en: "You'll classify galaxies into star-forming, Seyfert, and LINER regions and understand the physical drivers behind each category.",
      zh: "您将把星系分类为恒星形成、赛弗特和 LINER 区域，并理解每种类别背后的物理驱动因素。",
    },
  },
  {
    title: { en: "Spectral Line Fitting", zh: "谱线拟合" },
    desc: {
      en: "Load a spectrum, identify emission or absorption lines, fit Gaussian/Voigt profiles, and measure equivalent widths.",
      zh: "加载光谱，识别发射或吸收线，拟合高斯/Voigt 轮廓，测量等值宽度。",
    },
    outcome: {
      en: "You'll extract line centers, widths, and fluxes, and learn to estimate redshifts from line positions.",
      zh: "您将提取谱线中心、宽度和流量，学会从谱线位置估算红移。",
    },
  },
  {
    title: { en: "Multi-Wavelength SED Analysis", zh: "多波段 SED 分析" },
    desc: {
      en: "Collect photometry across UV, optical, and infrared bands from multiple catalogs and fit a spectral energy distribution model.",
      zh: "从多个星表收集紫外、光学和红外波段测光数据，拟合光谱能量分布模型。",
    },
    outcome: {
      en: "You'll determine stellar temperature, luminosity, and dust reddening E(B-V) from the best-fit SED model.",
      zh: "您将从最佳拟合 SED 模型中确定恒星温度、光度和尘埃红化 E(B-V)。",
    },
  },
  {
    title: { en: "Redshift Distribution of Quasars", zh: "类星体红移分布" },
    desc: {
      en: "Query large quasar catalogs, compute redshift distributions, and explore the cosmological implications.",
      zh: "查询大型类星体星表，计算红移分布，探索宇宙学含义。",
    },
    outcome: {
      en: "You'll create histograms and cumulative distributions showing how quasar density evolves with redshift.",
      zh: "您将创建直方图和累积分布图，展示类星体密度如何随红移演化。",
    },
  },
];

/* ── Main component ── */

export default function HelpPage() {
  const { lang } = useI18n();
  const [tab, setTab] = useState<Tab>("quickstart");
  const [glossaryFilter, setGlossaryFilter] = useState("");
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  const filteredGlossary = useMemo(() => {
    if (!glossaryFilter.trim()) return GLOSSARY;
    const q = glossaryFilter.toLowerCase();
    return GLOSSARY.filter(
      (g) =>
        g.term.toLowerCase().includes(q) ||
        g.en.toLowerCase().includes(q) ||
        g.zh.includes(glossaryFilter),
    );
  }, [glossaryFilter]);

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      {/* Header */}
      <div>
        <h1>{tx("Help & Learning Center", "帮助与学习中心", lang)}</h1>
        <p style={{ color: "var(--color-text-secondary)", maxWidth: 820 }}>
          {tx(
            "Everything you need to get started with astronomical research on Standard Astro.",
            "在 Standard Astro 上开始天文研究所需的一切。",
            lang,
          )}
        </p>
      </div>

      {/* Tabs */}
      <div className="page-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`page-tab${tab === t.key ? " active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {tx(t.en, t.zh, lang)}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "quickstart" && <QuickStartTab lang={lang} />}
      {tab === "tutorials" && <TutorialsTab lang={lang} />}
      {tab === "glossary" && (
        <GlossaryTab
          lang={lang}
          filter={glossaryFilter}
          setFilter={setGlossaryFilter}
          entries={filteredGlossary}
        />
      )}
      {tab === "faq" && (
        <FaqTab lang={lang} openIdx={openFaq} setOpenIdx={setOpenFaq} />
      )}
      {tab === "shortcuts" && <KeyboardShortcutsTab lang={lang} />}
    </div>
  );
}

/* ═══════════════════════════════════════
   Quick Start Tab
   ═══════════════════════════════════════ */

function QuickStartTab({ lang }: { lang: Lang }) {
  const steps: { icon: string; en: string; zh: string }[] = [
    {
      icon: "1",
      en: "Ask the AI Assistant to look up any astronomical object by name or coordinates. Try \"M31\" or \"10.68 41.27\" — results come back as tool cards with provenance.",
      zh: "让 AI 助手按名称或坐标查询任何天体。试试 \"M31\" 或 \"10.68 41.27\"——结果会以带溯源信息的工具卡片返回。",
    },
    {
      icon: "2",
      en: "Use the AI Assistant to ask questions in natural language — it can query databases, analyze data, and create plots for you.",
      zh: "使用 AI 助手用自然语言提问——它可以查询数据库、分析数据并为您创建图表。",
    },
    {
      icon: "3",
      en: "Try a Research Template: click one of the preset research projects and watch the AI walk you through a complete analysis.",
      zh: "尝试研究模板：点击预设研究项目之一，观看 AI 引导您完成完整分析。",
    },
    {
      icon: "4",
      en: "Export your results as LaTeX, Jupyter Notebook, or publication-ready figures for your papers and presentations.",
      zh: "将结果导出为 LaTeX、Jupyter Notebook 或可用于发表的图表，用于论文和报告。",
    },
  ];

  return (
    <div style={{ display: "grid", gap: "0.75rem" }}>
      <h2 style={{ margin: 0 }}>
        {tx("Get started in 5 minutes", "5 分钟快速入门", lang)}
      </h2>
      {steps.map((s, i) => (
        <section
          key={i}
          style={{
            display: "flex",
            gap: "1rem",
            alignItems: "flex-start",
            background: "var(--color-surface)",
            borderRadius: "var(--radius-md)",
            padding: "1rem 1.25rem",
            border: "1px solid var(--color-border)",
          }}
        >
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 32,
              height: 32,
              borderRadius: "50%",
              background: "var(--color-accent)",
              color: "#fff",
              fontWeight: 700,
              fontSize: "0.85rem",
              flexShrink: 0,
            }}
          >
            {s.icon}
          </span>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{tx(s.en, s.zh, lang)}</p>
        </section>
      ))}

      {/* Existing feature cards */}
      <h3 style={{ marginTop: "0.5rem", marginBottom: 0 }}>
        {tx("Feature Overview", "功能概览", lang)}
      </h3>
      <div
        style={{
          display: "grid",
          gap: "0.75rem",
          gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        }}
      >
        {/* M3 (2026-05-18) removed the Data Browser / ADQL / Pipeline pages;
            these cards describe the chat-driven workflows that replaced them. */}
        <FeatureCard
          title={tx("Archive Search", "档案库检索", lang)}
          desc={tx(
            "Ask the AI Assistant to search the connected archives by object name or coordinates. Results return as tool cards with provenance, and can be downloaded as CSV or VOTable.",
            "让 AI 助手按天体名称或坐标检索已接入的档案库。结果以带溯源信息的工具卡片返回，可下载为 CSV 或 VOTable。",
            lang,
          )}
        />
        <FeatureCard
          title="ADQL"
          desc={tx(
            "Ask the assistant to write and run ADQL/TAP queries; broad queries fall back to async TAP automatically. In Python, use get_adql_results() for the latest rows or get_adql_result_sets() for multi-query history.",
            "让助手编写并运行 ADQL/TAP 查询；较大的查询会自动转入异步 TAP。在 Python 中，最新结果用 get_adql_results()，多次查询历史用 get_adql_result_sets()。",
            lang,
          )}
        />
        <FeatureCard
          title={tx("Papers", "论文草稿", lang)}
          desc={tx(
            "Turn a chat session into a paper draft: the session's analysis is validated first, then a LaTeX draft with citations is generated and managed on the Papers page.",
            "把聊天会话变成论文草稿：先对会话中的分析做校验，然后生成带引用的 LaTeX 草稿，在论文页面管理。",
            lang,
          )}
        />
        <FeatureCard
          title={tx("AI Assistant", "AI 助手", lang)}
          desc={tx(
            "The sandbox includes NumPy, SciPy, Astropy, and plotting helpers. Run available_functions() in Python to see built-in utilities. State is isolated per chat session.",
            "沙盒已包含 NumPy、SciPy、Astropy 和绑图辅助工具。在 Python 中运行 available_functions() 查看内置工具。状态按聊天会话隔离。",
            lang,
          )}
        />
        <FeatureCard
          title={tx("Exports", "导出", lang)}
          desc={tx(
            "Search results and chats can be exported as Jupyter notebooks. Chat export converts run_python actions into code cells; markdown export preserves the conversation.",
            "搜索结果和聊天可以导出为 Jupyter notebook。聊天导出将 run_python 操作转换为代码单元；Markdown 导出保留对话内容。",
            lang,
          )}
        />
      </div>
    </div>
  );
}

function FeatureCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div
      style={{
        background: "var(--color-surface)",
        borderRadius: "var(--radius-md)",
        padding: "1rem 1.25rem",
        border: "1px solid var(--color-border)",
      }}
    >
      <h4 style={{ margin: "0 0 0.4rem 0", color: "var(--color-accent)" }}>
        {title}
      </h4>
      <p style={{ margin: 0, color: "var(--color-text-secondary)", fontSize: "0.88rem", lineHeight: 1.55 }}>
        {desc}
      </p>
    </div>
  );
}

/* ═══════════════════════════════════════
   Research Tutorials Tab
   ═══════════════════════════════════════ */

function TutorialsTab({ lang }: { lang: Lang }) {
  return (
    <div style={{ display: "grid", gap: "0.75rem" }}>
      <h2 style={{ margin: 0 }}>
        {tx("Research Templates", "研究模板", lang)}
      </h2>
      <p style={{ color: "var(--color-text-secondary)", margin: 0, maxWidth: 720 }}>
        {tx(
          "Each template guides you through a complete analysis using real data. Click a template in the AI Assistant to begin.",
          "每个模板使用真实数据引导您完成完整分析。在 AI 助手中点击模板即可开始。",
          lang,
        )}
      </p>
      {TUTORIALS.map((t, i) => (
        <div
          key={i}
          style={{
            background: "var(--color-surface)",
            borderRadius: "var(--radius-md)",
            padding: "1.25rem",
            border: "1px solid var(--color-border)",
          }}
        >
          <h3 style={{ margin: "0 0 0.4rem 0", color: "var(--color-accent)" }}>
            {tx(t.title.en, t.title.zh, lang)}
          </h3>
          <p style={{ margin: "0 0 0.5rem 0", lineHeight: 1.55 }}>
            {tx(t.desc.en, t.desc.zh, lang)}
          </p>
          <p
            style={{
              margin: 0,
              fontSize: "0.85rem",
              color: "var(--color-text-secondary)",
              fontStyle: "italic",
            }}
          >
            {tx("Outcome: ", "预期结果：", lang)}
            {tx(t.outcome.en, t.outcome.zh, lang)}
          </p>
        </div>
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════
   Glossary Tab
   ═══════════════════════════════════════ */

function GlossaryTab({
  lang,
  filter,
  setFilter,
  entries,
}: {
  lang: Lang;
  filter: string;
  setFilter: (v: string) => void;
  entries: typeof GLOSSARY;
}) {
  return (
    <div style={{ display: "grid", gap: "0.75rem" }}>
      <h2 style={{ margin: 0 }}>
        {tx("Astronomy Glossary", "天文术语表", lang)}
      </h2>

      {/* Search */}
      <input
        type="text"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder={tx(
          "Filter terms (e.g. redshift, parallax, FITS)...",
          "筛选术语（如红移、视差、FITS）...",
          lang,
        )}
        style={{
          width: "100%",
          maxWidth: 480,
          padding: "0.55rem 0.9rem",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--color-border)",
          background: "var(--color-surface)",
          color: "var(--color-text)",
          fontSize: "0.88rem",
          outline: "none",
        }}
      />

      <p style={{ margin: 0, color: "var(--color-text-tertiary)", fontSize: "0.82rem" }}>
        {tx(
          `Showing ${entries.length} of ${GLOSSARY.length} terms`,
          `显示 ${entries.length} / ${GLOSSARY.length} 个术语`,
          lang,
        )}
      </p>

      {/* Term list */}
      <div
        style={{
          display: "grid",
          gap: "1px",
          background: "var(--color-border)",
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
          border: "1px solid var(--color-border)",
        }}
      >
        {entries.map((g, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "220px 1fr",
              gap: "1rem",
              padding: "0.7rem 1rem",
              background: "var(--color-surface)",
              alignItems: "baseline",
            }}
          >
            <span
              style={{
                fontWeight: 600,
                color: "var(--color-accent)",
                fontSize: "0.88rem",
              }}
            >
              {g.term}
            </span>
            <span
              style={{
                color: "var(--color-text-secondary)",
                fontSize: "0.85rem",
                lineHeight: 1.5,
              }}
            >
              {lang === "zh" ? g.zh : g.en}
            </span>
          </div>
        ))}
        {entries.length === 0 && (
          <div
            style={{
              padding: "2rem",
              textAlign: "center",
              color: "var(--color-text-tertiary)",
              background: "var(--color-surface)",
            }}
          >
            {tx("No matching terms found.", "未找到匹配的术语。", lang)}
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   FAQ Tab
   ═══════════════════════════════════════ */

function FaqTab({
  lang,
  openIdx,
  setOpenIdx,
}: {
  lang: Lang;
  openIdx: number | null;
  setOpenIdx: (v: number | null) => void;
}) {
  return (
    <div style={{ display: "grid", gap: "0.75rem" }}>
      <h2 style={{ margin: 0 }}>
        {tx("Frequently Asked Questions", "常见问题", lang)}
      </h2>
      <div style={{ display: "grid", gap: "2px" }}>
        {FAQ_DATA.map((faq, i) => {
          const isOpen = openIdx === i;
          return (
            <div
              key={i}
              style={{
                background: "var(--color-surface)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
                overflow: "hidden",
              }}
            >
              <button
                onClick={() => setOpenIdx(isOpen ? null : i)}
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0.85rem 1rem",
                  background: "none",
                  border: "none",
                  color: "var(--color-text)",
                  fontSize: "0.9rem",
                  fontWeight: 600,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span>{tx(faq.q.en, faq.q.zh, lang)}</span>
                <span
                  style={{
                    flexShrink: 0,
                    marginLeft: "1rem",
                    transition: "transform 0.2s ease",
                    transform: isOpen ? "rotate(180deg)" : "rotate(0)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  ▾
                </span>
              </button>
              {isOpen && (
                <div
                  style={{
                    padding: "0 1rem 0.85rem 1rem",
                    color: "var(--color-text-secondary)",
                    fontSize: "0.88rem",
                    lineHeight: 1.6,
                  }}
                >
                  {tx(faq.a.en, faq.a.zh, lang)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   Keyboard Shortcuts Tab
   ═══════════════════════════════════════ */

interface Shortcut {
  keys: string;
  desc: { en: string; zh: string };
}

// M3 (2026-05-18): the Pipeline-editor Undo/Redo rows were removed together
// with the Pipeline page — do not document shortcuts for deleted surfaces.
const SHORTCUTS: Shortcut[] = [
  { keys: "Cmd/Ctrl + K", desc: { en: "Open command palette", zh: "打开命令面板" } },
  { keys: "Escape", desc: { en: "Close dialogs, panels, and command palette", zh: "关闭对话框、面板和命令面板" } },
  {
    keys: "Enter",
    desc: { en: "Send message (Chat) / Execute command (Command palette)", zh: "发送消息（聊天）/ 执行命令（命令面板）" },
  },
  { keys: "Shift + Enter", desc: { en: "New line in chat input", zh: "在聊天输入中换行" } },
  { keys: "Tab", desc: { en: "Navigate between interactive elements", zh: "在交互元素之间导航" } },
  { keys: "Arrow Up / Down", desc: { en: "Navigate command palette results", zh: "浏览命令面板结果" } },
];

function KeyboardShortcutsTab({ lang }: { lang: Lang }) {
  return (
    <div style={{ display: "grid", gap: "0.75rem" }}>
      <h2 style={{ margin: 0 }}>
        {tx("Keyboard Shortcuts", "快捷键", lang)}
      </h2>
      <p style={{ color: "var(--color-text-secondary)", margin: 0, maxWidth: 720 }}>
        {tx(
          "Use these shortcuts to work faster across the platform.",
          "使用这些快捷键提高平台操作效率。",
          lang,
        )}
      </p>

      <div
        style={{
          display: "grid",
          gap: "1px",
          background: "var(--color-border)",
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
          border: "1px solid var(--color-border)",
        }}
      >
        {/* Header row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "280px 1fr",
            gap: "1rem",
            padding: "0.7rem 1rem",
            background: "var(--color-surface)",
            fontWeight: 600,
            fontSize: "0.85rem",
            color: "var(--color-text-secondary)",
          }}
        >
          <span>{tx("Shortcut", "快捷键", lang)}</span>
          <span>{tx("Action", "操作", lang)}</span>
        </div>

        {/* Shortcut rows */}
        {SHORTCUTS.map((s, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "280px 1fr",
              gap: "1rem",
              padding: "0.7rem 1rem",
              background: "var(--color-surface)",
              alignItems: "baseline",
            }}
          >
            <span>
              {s.keys.split(" / ").map((k, j) => (
                <span key={j}>
                  {j > 0 && (
                    <span
                      style={{
                        color: "var(--color-text-tertiary)",
                        fontSize: "0.82rem",
                        margin: "0 0.35rem",
                      }}
                    >
                      or
                    </span>
                  )}
                  <kbd
                    style={{
                      display: "inline-block",
                      padding: "0.15rem 0.45rem",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--color-border)",
                      background: "var(--color-bg, #f5f5f5)",
                      fontFamily: "inherit",
                      fontSize: "0.82rem",
                      fontWeight: 600,
                      color: "var(--color-accent)",
                      lineHeight: 1.6,
                    }}
                  >
                    {k.trim()}
                  </kbd>
                </span>
              ))}
            </span>
            <span
              style={{
                color: "var(--color-text-secondary)",
                fontSize: "0.85rem",
                lineHeight: 1.5,
              }}
            >
              {tx(s.desc.en, s.desc.zh, lang)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
