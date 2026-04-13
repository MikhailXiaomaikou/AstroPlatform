import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";

interface Command {
  id: string;
  label: string;
  category: string;
  action: () => void;
  keywords?: string;
}

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const commands: Command[] = [
    // Navigation
    { id: "nav-search", label: "Search Objects", category: "Navigation", action: () => navigate("/"), keywords: "data browser find" },
    { id: "nav-chat", label: "AI Assistant", category: "Navigation", action: () => navigate("/chat"), keywords: "ask question" },
    { id: "nav-pipeline", label: "Pipeline Studio", category: "Navigation", action: () => navigate("/pipeline"), keywords: "workflow dag" },
    { id: "nav-adql", label: "ADQL Query", category: "Navigation", action: () => navigate("/adql"), keywords: "sql tap" },
    { id: "nav-workspace", label: "Workspace", category: "Navigation", action: () => navigate("/workspace"), keywords: "files saved" },
    { id: "nav-team", label: "Team", category: "Navigation", action: () => navigate("/team"), keywords: "collaborate share" },
    { id: "nav-alerts", label: "Transient Alerts", category: "Navigation", action: () => navigate("/alerts"), keywords: "transient supernova" },
    { id: "nav-anomalies", label: "Anomaly Explorer", category: "Navigation", action: () => navigate("/anomalies"), keywords: "outlier detection" },
    { id: "nav-settings", label: "Settings", category: "Navigation", action: () => navigate("/settings"), keywords: "config preferences" },
    { id: "nav-help", label: "Help & Docs", category: "Navigation", action: () => navigate("/help"), keywords: "documentation guide" },
    // Actions
    { id: "act-new-chat", label: "New Chat Session", category: "Actions", action: () => { localStorage.setItem("astro_chat_new_session", "1"); navigate("/chat"); }, keywords: "create conversation" },
    { id: "act-new-pipeline", label: "New Pipeline", category: "Actions", action: () => navigate("/pipeline"), keywords: "create workflow" },
  ];

  // Filter commands by substring match
  const filtered = query
    ? commands.filter((c) => {
        const search = query.toLowerCase();
        return (
          c.label.toLowerCase().includes(search) ||
          (c.keywords || "").toLowerCase().includes(search) ||
          c.category.toLowerCase().includes(search)
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
    (groups[cmd.category] ||= []).push(cmd);
  }

  return (
    <div className="cmd-palette-backdrop" onClick={() => setOpen(false)}>
      <div className="cmd-palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="cmd-palette-input"
          placeholder="Type a command..."
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={handleKeyDown}
        />
        <div className="cmd-palette-results">
          {filtered.length === 0 && (
            <div className="cmd-palette-empty">No matching commands</div>
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
                    {cmd.label}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="cmd-palette-footer">
          <span>&uarr;&darr; Navigate</span>
          <span>&crarr; Select</span>
          <span>Esc Close</span>
        </div>
      </div>
    </div>
  );
}
