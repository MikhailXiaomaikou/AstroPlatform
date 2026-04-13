import { useEffect, useState } from "react";
import { getOperationLog, clearOperationLog } from "../../api/client";
import type { ProviderMeta } from "../../api/client";

const PROVIDERS: Record<string, ProviderMeta> = {
  anthropic: { name: "Anthropic (Claude)", prefix: "sk-ant-" },
  openai: { name: "OpenAI (GPT)", prefix: "sk-" },
  google: { name: "Google (Gemini)", prefix: "AI" },
  deepseek: { name: "DeepSeek", prefix: "sk-" },
  custom: { name: "Custom / Other", prefix: "" },
};
const CHAT_PROVIDERS = ["anthropic", "openai", "deepseek"] as const;

function getStoredKeys(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem("astro_api_keys") || "{}");
  } catch {
    return {};
  }
}

function saveStoredKeys(keys: Record<string, string>) {
  localStorage.setItem("astro_api_keys", JSON.stringify(keys));
}

function getStoredPreferredProvider(): string {
  try {
    return localStorage.getItem("astro_ai_provider") || "anthropic";
  } catch {
    return "anthropic";
  }
}

function saveStoredPreferredProvider(provider: string) {
  localStorage.setItem("astro_ai_provider", provider);
}

export default function SettingsPage() {
  const [keys, setKeys] = useState<Record<string, string>>(getStoredKeys);
  const [selectedProvider, setSelectedProvider] = useState("anthropic");
  const [preferredProvider, setPreferredProvider] = useState(getStoredPreferredProvider);
  const [keyInput, setKeyInput] = useState("");
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    saveStoredKeys(keys);
  }, [keys]);

  useEffect(() => {
    saveStoredPreferredProvider(preferredProvider);
  }, [preferredProvider]);

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    const next = { ...keys, [selectedProvider]: key };
    setKeys(next);
    if (CHAT_PROVIDERS.includes(selectedProvider as typeof CHAT_PROVIDERS[number])) {
      setPreferredProvider(selectedProvider);
    }
    setKeyInput("");
    setMessage({ type: "ok", text: `${PROVIDERS[selectedProvider]?.name || selectedProvider} key saved.` });
  }

  function handleDelete(provider: string) {
    const next = { ...keys };
    delete next[provider];
    setKeys(next);
    if (preferredProvider === provider) {
      const fallback = CHAT_PROVIDERS.find((candidate) => Boolean(next[candidate])) || "anthropic";
      setPreferredProvider(fallback);
    }
    setMessage({ type: "ok", text: `${PROVIDERS[provider]?.name || provider} key removed.` });
  }

  function maskKey(key: string): string {
    if (key.length <= 12) return key.slice(0, 3) + "..." + key.slice(-3);
    return key.slice(0, 8) + "..." + key.slice(-4);
  }

  const configuredKeys = Object.entries(keys).filter(([, v]) => v);

  return (
    <div className="settings-page">
      <h1>Settings</h1>

      <section className="settings-section">
        <h2>AI API Keys</h2>
        <p className="settings-desc">
          Configure API keys for AI providers. The AI assistant can route across Anthropic, OpenAI,
          and DeepSeek based on your preferred provider and available keys. Keys are stored in your browser locally.
        </p>

        <div className="settings-key-form" style={{ marginBottom: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span>Preferred AI provider for chat</span>
            <select
              value={preferredProvider}
              onChange={(e) => setPreferredProvider(e.target.value)}
              className="settings-select"
            >
              {CHAT_PROVIDERS.map((provider) => (
                <option key={provider} value={provider}>
                  {PROVIDERS[provider]?.name || provider}{keys[provider] ? "" : " (no key saved)"}
                </option>
              ))}
            </select>
          </label>
        </div>

        {configuredKeys.length > 0 && (
          <div className="settings-keys-list">
            {configuredKeys.map(([provider, key]) => (
              <div key={provider} className="settings-key-row">
                <span className="settings-key-provider">{PROVIDERS[provider]?.name || provider}</span>
                <code className="settings-key-masked">{maskKey(key)}</code>
                <button className="btn-danger-sm" onClick={() => handleDelete(provider)}>
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}

        <form className="settings-key-form" onSubmit={handleSave}>
          <select
            value={selectedProvider}
            onChange={(e) => setSelectedProvider(e.target.value)}
            className="settings-select"
          >
            {Object.entries(PROVIDERS).map(([id, meta]) => (
              <option key={id} value={id}>
                {meta.name}{keys[id] ? " (update)" : ""}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder={PROVIDERS[selectedProvider]?.prefix + "..."}
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            className="settings-input"
            autoComplete="off"
          />
          <button type="submit" className="btn-primary" disabled={!keyInput.trim()}>
            {keys[selectedProvider] ? "Update" : "Save"}
          </button>
        </form>

        {message && (
          <p className={`settings-msg settings-msg-${message.type}`}>{message.text}</p>
        )}
      </section>

      <CoordinateConverter />
      <OperationLogSection />
    </div>
  );
}

function CoordinateConverter() {
  const [raInput, setRaInput] = useState("");
  const [decInput, setDecInput] = useState("");
  const [result, setResult] = useState<string | null>(null);

  function decToHMS(deg: number): string {
    const h = deg / 15;
    const hh = Math.floor(h);
    const mm = Math.floor((h - hh) * 60);
    const ss = ((h - hh) * 60 - mm) * 60;
    return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${ss.toFixed(2)}`;
  }

  function decToDMS(deg: number): string {
    const sign = deg < 0 ? "-" : "+";
    const absDeg = Math.abs(deg);
    const dd = Math.floor(absDeg);
    const mm = Math.floor((absDeg - dd) * 60);
    const ss = ((absDeg - dd) * 60 - mm) * 60;
    return `${sign}${String(dd).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${ss.toFixed(1)}`;
  }

  function hmsToDecRA(hms: string): number | null {
    const parts = hms.trim().split(/[:\shms]+/).filter(Boolean);
    if (parts.length < 2) return null;
    const h = parseFloat(parts[0]);
    const m = parseFloat(parts[1]);
    const s = parts.length > 2 ? parseFloat(parts[2]) : 0;
    if (isNaN(h) || isNaN(m) || isNaN(s)) return null;
    return (h + m / 60 + s / 3600) * 15;
  }

  function dmsToDecDec(dms: string): number | null {
    const cleaned = dms.trim();
    const signMatch = cleaned.match(/^([+-])/);
    const sign = signMatch && signMatch[1] === "-" ? -1 : 1;
    const parts = cleaned.replace(/^[+-]/, "").split(/[:\sdm°'"s]+/).filter(Boolean);
    if (parts.length < 2) return null;
    const d = parseFloat(parts[0]);
    const m = parseFloat(parts[1]);
    const s = parts.length > 2 ? parseFloat(parts[2]) : 0;
    if (isNaN(d) || isNaN(m) || isNaN(s)) return null;
    return sign * (d + m / 60 + s / 3600);
  }

  function handleConvert() {
    const raStr = raInput.trim();
    const decStr = decInput.trim();
    if (!raStr || !decStr) return;

    // Try decimal first
    const raDec = parseFloat(raStr);
    const decDec = parseFloat(decStr);

    if (!isNaN(raDec) && !isNaN(decDec) && !raStr.includes(":") && !decStr.includes(":")) {
      // Decimal -> HMS/DMS
      setResult(`RA: ${decToHMS(raDec)}    Dec: ${decToDMS(decDec)}`);
    } else {
      // HMS/DMS -> Decimal
      const raVal = hmsToDecRA(raStr);
      const decVal = dmsToDecDec(decStr);
      if (raVal !== null && decVal !== null) {
        setResult(`RA: ${raVal.toFixed(6)} deg    Dec: ${decVal.toFixed(6)} deg`);
      } else {
        setResult("Could not parse input. Use decimal degrees or HH:MM:SS / DD:MM:SS format.");
      }
    }
  }

  return (
    <section className="settings-section">
      <h2>Coordinate Converter</h2>
      <p className="settings-desc">
        Convert between decimal degrees and HMS/DMS format.
        Enter decimal degrees to get HMS/DMS, or HMS/DMS (HH:MM:SS / +DD:MM:SS) to get decimal.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", fontSize: "0.8rem" }}>
          RA
          <input
            type="text"
            value={raInput}
            onChange={(e) => setRaInput(e.target.value)}
            placeholder="180.0 or 12:00:00"
            className="settings-input"
            style={{ width: 160 }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: "0.8rem" }}>
          Dec
          <input
            type="text"
            value={decInput}
            onChange={(e) => setDecInput(e.target.value)}
            placeholder="45.0 or +45:00:00"
            className="settings-input"
            style={{ width: 160 }}
          />
        </label>
        <button className="btn-primary" onClick={handleConvert} style={{ height: 36 }}>
          Convert
        </button>
      </div>
      {result && (
        <p style={{ marginTop: 8, fontFamily: "monospace", fontSize: "0.9rem" }}>{result}</p>
      )}
    </section>
  );
}

function OperationLogSection() {
  const [log, setLog] = useState(getOperationLog);

  function handleExportMarkdown() {
    const lines = ["## Research Log", ""];
    for (const entry of log) {
      const ts = new Date(entry.timestamp).toLocaleString();
      lines.push(`- **${ts}** -- [${entry.type}] ${entry.detail}`);
    }
    const md = lines.join("\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "research_log.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleClear() {
    clearOperationLog();
    setLog([]);
  }

  return (
    <section className="settings-section">
      <h2>Research Operation Log</h2>
      <p className="settings-desc">
        A record of your research actions (searches, queries, exports, visualizations).
        Export as Markdown for inclusion in your paper&apos;s Methods section.
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button className="btn-primary" onClick={handleExportMarkdown} disabled={log.length === 0}>
          Export Markdown
        </button>
        <button className="btn-secondary" onClick={handleClear} disabled={log.length === 0}>
          Clear Log
        </button>
        <button className="btn-secondary" onClick={() => setLog(getOperationLog())}>
          Refresh
        </button>
      </div>
      {log.length === 0 ? (
        <p style={{ color: "rgba(255,255,255,0.5)", fontSize: "0.85rem" }}>No operations logged yet.</p>
      ) : (
        <div style={{ maxHeight: 300, overflowY: "auto", fontSize: "0.8rem" }}>
          {log.slice().reverse().map((entry, i) => (
            <div key={i} style={{ padding: "4px 0", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <span style={{ color: "rgba(255,255,255,0.5)", marginRight: 8 }}>
                {new Date(entry.timestamp).toLocaleString()}
              </span>
              <span style={{ color: "rgba(56,189,248,0.9)", marginRight: 8 }}>[{entry.type}]</span>
              <span>{entry.detail}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
