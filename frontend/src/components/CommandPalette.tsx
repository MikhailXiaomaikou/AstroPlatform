import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import { NAV_ROUTES } from "../routes";

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

  const commands = useMemo<Command[]>(() => {
    // Navigation entries come from the single source of truth (frontend/src/routes.ts).
    // History: prior to that constant, this array contained 5 routes that no
    // longer exist (/search, /pipeline, /adql, /workspace, /anomalies) — Cmd+K
    // navigated users to 404s.
    const navCommands: Command[] = NAV_ROUTES.map((route) => ({
      id: route.id,
      labelKey: route.labelKey,
      categoryKey: route.categoryKey,
      action: () => navigate(route.path),
      keywords: route.keywords,
    }));
    // Actions — kept inline because they have side effects beyond navigation.
    const actionCommands: Command[] = [
      {
        id: "act-new-chat",
        labelKey: "cmd.new_chat",
        categoryKey: "cmd.cat_action",
        action: () => {
          localStorage.setItem("astro_chat_new_session", "1");
          navigate("/chat");
        },
        keywords: "create conversation",
      },
    ];
    return [...navCommands, ...actionCommands];
  }, [navigate]);

  // Filter commands by substring match on translated label, keywords, and category
  const filtered = useMemo(() => {
    if (!query) return commands;
    const search = query.toLowerCase();
    return commands.filter((c) =>
      t(c.labelKey).toLowerCase().includes(search) ||
      (c.keywords || "").toLowerCase().includes(search) ||
      t(c.categoryKey).toLowerCase().includes(search)
    );
  }, [query, commands, t]);

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
    <div className="cmd-palette-backdrop" role="dialog" aria-modal="true" aria-label="Command palette" onClick={() => setOpen(false)}>
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
          aria-label="Search commands"
        />
        <div className="cmd-palette-results" role="listbox">
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
                    role="option"
                    aria-selected={globalIdx === selectedIndex}
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
