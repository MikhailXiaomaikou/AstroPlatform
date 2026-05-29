import { type ChatAction } from "../../api/client";

// Maps each cosmology / research tool to a human-readable method-step title.
// Membership here also defines what counts as a "research turn" (isResearchTurn).
const STEP_TITLES: Record<string, string> = {
  list_cosmology_datasets: "Data selection",
  load_cosmology_data_product: "Data product load",
  build_cosmology_likelihood: "Likelihood configuration",
  build_cosmology_robustness_matrix: "Robustness-matrix configuration",
  run_cosmology_likelihood_chain: "Likelihood chain",
  run_cosmology_robustness_matrix: "Robustness-matrix run",
  run_cmb_rotation_likelihood: "CMB-rotation likelihood",
  fit_cosmology_mcmc: "MCMC fit",
  run_cobaya_cosmology: "External Cobaya run",
  assess_bao_bin_anomaly: "Alcock-Paczynski geometric test",
  compare_luminosity_distances: "Luminosity-distance comparison",
  audit_published_constraint: "Published-constraint audit",
  plan_research_program: "Research plan",
  run_research_matrix: "Experiment matrix",
  build_evidence_graph: "Evidence graph",
  verify_research_facts: "Fact check",
  export_research_report: "Report export",
};

const RESEARCH_TOOLS = new Set(Object.keys(STEP_TITLES));

export function isResearchTurn(actions: ChatAction[] | undefined): boolean {
  return !!actions && actions.some((a) => RESEARCH_TOOLS.has(String(a.action)));
}

function toolResult(action: ChatAction): Record<string, unknown> | undefined {
  const r = (action as Record<string, unknown>).tool_result;
  return r && typeof r === "object" ? (r as Record<string, unknown>) : undefined;
}

function num(v: unknown): string | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 4 : 2).replace(/\.?0+$/, "");
}

function getMedian(params: unknown, key: string): string | null {
  if (!params || typeof params !== "object") return null;
  const p = (params as Record<string, unknown>)[key];
  if (!p || typeof p !== "object") return null;
  return num((p as Record<string, unknown>).median);
}

// One-line, tool-grounded result string per step (read straight off the tool
// result; never invents anything — falls back to "" when nothing quotable).
function stepResultLine(tool: string, r: Record<string, unknown> | undefined): string {
  if (!r) return "";
  switch (tool) {
    case "list_cosmology_datasets": {
      const ds = r.datasets;
      if (Array.isArray(ds)) {
        const names = ds
          .map((d) => (d && typeof d === "object" ? String((d as Record<string, unknown>).display_name || (d as Record<string, unknown>).key || "") : ""))
          .filter(Boolean);
        return `${names.length} dataset(s): ${names.slice(0, 4).join(", ")}${names.length > 4 ? "…" : ""}`;
      }
      return "";
    }
    case "build_cosmology_likelihood":
    case "build_cosmology_robustness_matrix": {
      const model = r.model || r.model_label;
      return model ? `model = ${String(model)}` : "";
    }
    case "run_cosmology_likelihood_chain":
    case "run_cosmology_robustness_matrix":
    case "fit_cosmology_mcmc": {
      const parts: string[] = [];
      for (const [label, key] of [["H0", "H0"], ["Ωm", "omegam"], ["σ8", "sigma8"], ["w", "w"]] as const) {
        const m = getMedian(r.parameters, key);
        if (m) parts.push(`${label}=${m}`);
      }
      const tier = r.chain_tier ? ` · ${String(r.chain_tier)}` : "";
      return parts.length ? parts.join(", ") + tier : (r.chain_tier ? String(r.chain_tier) : "");
    }
    case "assess_bao_bin_anomaly": {
      const om = num(r.omega_m_best);
      const lo = num(r.omega_m_1sigma_low);
      const hi = num(r.omega_m_1sigma_high);
      if (om) return `Ωm = ${om}${lo && hi ? ` (1σ ${lo}–${hi})` : ""}`;
      return "";
    }
    case "compare_luminosity_distances": {
      const s = r.summary;
      if (s && typeof s === "object") {
        const d = num((s as Record<string, unknown>).max_abs_delta_pct);
        if (d) return `max |ΔD_L| = ${d}%`;
      }
      return "";
    }
    case "plan_research_program": {
      const probes = (r.research_plan as Record<string, unknown> | undefined)?.required_probes;
      return Array.isArray(probes) ? `probes: ${probes.join(", ")}` : "";
    }
    case "run_research_matrix": {
      const ready = num(r.ready_cells);
      const total = num(r.matrix_size);
      return ready && total ? `${ready}/${total} cells publication-ready` : "";
    }
    case "verify_research_facts": {
      const v = num(r.verified_claim_count);
      const u = num(r.unsupported_claim_count);
      const bits: string[] = [];
      if (v) bits.push(`${v} verified`);
      if (u && Number(u) > 0) bits.push(`${u} held`);
      return bits.join(", ");
    }
    default:
      return "";
  }
}

type StepTone = "ok" | "partial" | "failed";

function stepTone(r: Record<string, unknown> | undefined): StepTone {
  if (!r) return "ok";
  const status = String(r.__tool_status__ || r.analysis_status || "").toUpperCase();
  if (status === "FAILED" || status === "SYNTHETIC" || r.success === false) return "failed";
  if (status === "EMPTY" || status === "PARTIAL" || status === "UNAVAILABLE" || status === "EXPLORATORY") return "partial";
  return "ok";
}

const TONE_COLOR: Record<StepTone, string> = {
  ok: "#2e6a4e", // forest green
  partial: "#b8860b", // amber
  failed: "#7b2d26", // burgundy
};
const TONE_GLYPH: Record<StepTone, string> = { ok: "✓", partial: "⚠", failed: "✕" };

export default function ResearchStepsCard({ actions }: { actions: ChatAction[] }) {
  const steps = actions.map((action) => {
    const tool = String(action.action);
    const r = toolResult(action);
    return {
      title: STEP_TITLES[tool] || tool,
      detail: stepResultLine(tool, r),
      tone: stepTone(r),
    };
  });
  if (!steps.length) return null;
  return (
    <div
      className="research-steps-card"
      style={{
        border: "1px solid rgba(15,23,42,0.10)",
        borderRadius: 10,
        background: "#fbfaf5",
        padding: "10px 12px",
        margin: "8px 0",
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 13, color: "#1a1a1a", marginBottom: 6 }}>
        🔬 Research method — {steps.length} step{steps.length === 1 ? "" : "s"}
      </div>
      <ol style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
        {steps.map((s, i) => (
          <li
            key={i}
            style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "3px 0", borderTop: i ? "1px solid rgba(15,23,42,0.05)" : "none" }}
          >
            <span style={{ color: "#6b7280", fontVariantNumeric: "tabular-nums", minWidth: 18 }}>{i + 1}.</span>
            <span style={{ color: TONE_COLOR[s.tone], fontWeight: 700, minWidth: 14 }}>{TONE_GLYPH[s.tone]}</span>
            <span style={{ flex: 1 }}>
              <span style={{ fontWeight: 500, color: "#1a1a1a" }}>{s.title}</span>
              {s.detail && <span style={{ color: "#475569" }}> — {s.detail}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
