// Per-assistant-message validation badge (2026-07-03 honesty surfacing).
//
// Renders the compact validation_summary the backend gate stack attaches to
// each reply. HONESTY RULE: never render a stronger claim than the machinery
// guarantees — a passed gate means "validated against this turn's tool
// data", NOT "guaranteed true"; skipped / blocked / not-run states must be
// visibly distinct from passed. Messages without a summary render nothing.
//
// overallValidationState is co-exported so tests can lock the state mapping
// (same accepted HMR trade-off as ChatPage's NEXT_STEPS_PANEL_ACTIONS).
/* eslint-disable react-refresh/only-export-components */
import type { ValidationSummary } from "../../api/client";
import { useI18n } from "../../i18n";

type OverallState = "blocked" | "limited" | "regenerated" | "passed" | "not_validated";

export function overallValidationState(summary: ValidationSummary): OverallState {
  const states = [summary.numeric_gate, summary.citation_gate];
  if (summary.blocked || states.includes("blocked")) return "blocked";
  if (summary.limited || states.includes("limited")) return "limited";
  if (states.includes("regenerated") || (summary.regen_count ?? 0) > 0) {
    return "regenerated";
  }
  // "passed" requires the numeric gate to have actually validated against
  // tool data; every skipped/not_run variant is "not validated".
  if (summary.numeric_gate === "passed") return "passed";
  return "not_validated";
}

const BADGE_STYLE: Record<OverallState, { icon: string; color: string; border: string; background: string }> = {
  passed: { icon: "✓", color: "#1b5e20", border: "#a5d6a7", background: "#f1f8f1" },
  regenerated: { icon: "⚠", color: "#8a6a00", border: "#e0c36a", background: "#fff8e6" },
  limited: { icon: "⚠", color: "#8a6a00", border: "#e0c36a", background: "#fff8e6" },
  blocked: { icon: "⛔", color: "#b00020", border: "#f2b8bf", background: "#fdf2f4" },
  not_validated: { icon: "◌", color: "#5f6368", border: "#d0d3d8", background: "#f6f7f8" },
};

function GateStateLine({ label, state }: { label: string; state?: string }) {
  const { t } = useI18n();
  const known = new Set([
    "passed", "regenerated", "limited", "blocked", "skipped_no_data", "skipped", "not_run",
  ]);
  const stateLabel = state && known.has(state)
    ? t(`chat.validation.state_${state}`)
    : state || t("chat.validation.state_not_run");
  return (
    <div>
      <strong>{label}:</strong> <span>{stateLabel}</span>
    </div>
  );
}

export function ValidationBadge({
  summary,
  truncated,
}: {
  summary?: ValidationSummary;
  truncated?: boolean;
}) {
  const { t } = useI18n();
  if (!summary && !truncated) return null;

  const state = summary ? overallValidationState(summary) : null;
  const style = state ? BADGE_STYLE[state] : null;
  const badgeLabel = state ? t(`chat.validation.badge_${state}`) : "";
  const regenCount = summary?.regen_count ?? 0;
  const interventions = (summary?.interventions ?? []).filter(
    (item) => item && typeof item === "object",
  );

  return (
    <div
      className="chat-validation-badge"
      style={{ marginTop: 6, fontSize: "0.74rem", display: "flex", flexWrap: "wrap", gap: 6, alignItems: "flex-start" }}
    >
      {summary && state && style && (
        <details style={{ display: "inline-block" }}>
          <summary
            data-validation-state={state}
            title={t("chat.validation.tooltip")}
            style={{
              cursor: "pointer",
              listStyle: "none",
              display: "inline-block",
              padding: "1px 8px",
              borderRadius: 10,
              border: `1px solid ${style.border}`,
              background: style.background,
              color: style.color,
            }}
          >
            {style.icon} {badgeLabel}
            {summary.reason && state === "not_validated" ? ` (${summary.reason})` : ""}
          </summary>
          <div
            className="chat-validation-detail"
            style={{
              marginTop: 4,
              padding: "6px 10px",
              border: "1px solid var(--color-border, #d0d3d8)",
              borderRadius: 6,
              background: "var(--color-bg-secondary, #fafafa)",
              color: "var(--color-text-secondary, #444)",
              lineHeight: 1.5,
            }}
          >
            <GateStateLine
              label={t("chat.validation.numeric_gate")}
              state={summary.numeric_gate}
            />
            <GateStateLine
              label={t("chat.validation.citation_gate")}
              state={summary.citation_gate}
            />
            {regenCount > 0 && (
              <div>{t("chat.validation.regen_count").replace("{n}", String(regenCount))}</div>
            )}
            {interventions.length > 0 && (
              <div>
                <strong>{t("chat.validation.interventions")}:</strong>{" "}
                {interventions
                  .map((item) => `${item.gate || "?"}→${item.action || "?"}`)
                  .join(", ")}
              </div>
            )}
            <div style={{ marginTop: 2, fontStyle: "italic" }}>
              {t("chat.validation.tooltip")}
            </div>
          </div>
        </details>
      )}
      {truncated && (
        <span
          className="chat-truncation-note"
          style={{
            display: "inline-block",
            padding: "1px 8px",
            borderRadius: 10,
            border: "1px solid #e0c36a",
            background: "#fff8e6",
            color: "#8a6a00",
          }}
        >
          ✂ {t("chat.validation.truncated")}
        </span>
      )}
    </div>
  );
}
