import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";

interface Command {
  id: string;
  labelKey: string;
  categoryKey: string;
  action: () => void;
  keywords?: string;
}

export default function CommandPalette() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const commands: Command[] = [
    // Navigation
    { id: "nav-search", labelKey: "cmd.search_objects", categoryKey: "cmd.cat_nav", action: () => navigate("/"), keywords: "data browser find" },
    { id: "nav-chat", labelKey: "cmd.ai_assistant", categoryKey: "cmd.cat_nav", action: () => navigate("/chat"), keywords: "ask question" },
    { id: "nav-pipeline", labelKey: "cmd.pipeline_studio", categoryKey: "cmd.cat_nav", action: () => navigate("/pipeline"), keywords: "workflow dag" },
    { id: "nav-adql", labelKey: "cmd.adql_query", categoryKey: "cmd.cat_nav", action: () => navigate("/adql"), keywords: "sql tap" },
    { id: "nav-workspace", labelKey: "cmd.workspace", categoryKey: "cmd.cat_nav", action: () => navigate("/workspace"), keywords: "files saved" },
    { id: "nav-team", labelKey: "cmd.team", categoryKey: "cmd.cat_nav", action: () => navigate("/team"), keywords: "collaborate share" },
    { id: "nav-alerts", labelKey: "cmd.alerts", categoryKey: "cmd.cat_nav", action: () => navigate("/alerts"), keywords: "transient supernova" },
    { id: "nav-anomalies", labelKey: "cmd.anomalies", categoryKey: "cmd.cat_nav", action: () => navigate("/anomalies"), keywords: "outlier detection" },
    { id: "nav-settings", labelKey: "cmd.settings", categoryKey: "cmd.cat_nav", action: () => navigate("/settings"), keywords: "config preferences" },
    { id: "nav-help", labelKey: "cmd.help", categoryKey: "cmd.cat_nav", action: () => navigate("/help"), keywords: "documentation guide" },
    // Actions
    { id: "act-new-chat", labelKey: "cmd.new_chat", categoryKey: "cmd.cat_action", action: () => { localStorage.setItem("astro_chat_new_session", "1"); navigate("/chat"); }, keywords: "create conversation" },
    { id: "act-new-pipeline", labelKey: "cmd.new_pipeline", categoryKey: "cmd.cat_action", action: () => navigate("/pipeline"), keywords: "create workflow" },
  ];

  // Filter commands by substring match on translated label, keywords, and category
  const filtered = query
    ? commands.filter((c) => {
        const search = query.toLowerCase();
        return (
          t(c.labelKey).toLowerCase().includes(search) ||
          (c.keywords || "").toLowerCase().includes(search) ||
          t(c.categoryKey).toLowerCase().includes(search)
        );
      })
    : commands;

  // Global Cmd+K / Ctrl+K handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
        setQuery("");
        setSelectedIndex(0);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Focus input when opened
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" && filtered[selectedIndex]) {
        filtered[selectedIndex].action();
        setOpen(false);
      }
    },
    [filtered, selectedIndex],
  );

  if (!open) return null;

  // Group filtered commands by category
  const groups: Record<string, Command[]> = {};
  for (const cmd of filtered) {
    const cat = t(cmd.categoryKey);
    (groups[cat] ||= []).push(cmd);
  }

  return (
    <div className="cmd-palette-backdrop" onClick={() => setOpen(false)}>
      <div className="cmd-palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="cmd-palette-input"
          placeholder={t("cmd.placeholder")}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={handleKeyDown}
        />
        <div className="cmd-palette-results">
          {filtered.length === 0 && (
            <div className="cmd-palette-empty">{t("cmd.no_match")}</div>
          )}
          {Object.entries(groups).map(([category, cmds]) => (
            <div key={category}>
              <div className="cmd-palette-category">{category}</div>
              {cmds.map((cmd) => {
                const globalIdx = filtered.indexOf(cmd);
                return (
                  <div
                    key={cmd.id}
                    className={`cmd-palette-item${globalIdx === selectedIndex ? " selected" : ""}`}
                    onClick={() => {
                      cmd.action();
                      setOpen(false);
                    }}
                    onMouseEnter={() => setSelectedIndex(globalIdx)}
                  >
                    {t(cmd.labelKey)}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="cmd-palette-footer">
          <span>&uarr;&darr; {t("cmd.navigate")}</span>
          <span>&crarr; {t("cmd.select")}</span>
          <span>Esc {t("common.close")}</span>
        </div>
      </div>
    </div>
  );
}
