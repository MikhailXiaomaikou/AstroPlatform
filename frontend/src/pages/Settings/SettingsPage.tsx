import { useEffect, useState } from "react";
import type { ProviderMeta } from "../../api/client";

const PROVIDERS: Record<string, ProviderMeta> = {
  anthropic: { name: "Anthropic (Claude)", prefix: "sk-ant-" },
  openai: { name: "OpenAI (GPT)", prefix: "sk-" },
  google: { name: "Google (Gemini)", prefix: "AI" },
  deepseek: { name: "DeepSeek", prefix: "sk-" },
  custom: { name: "Custom / Other", prefix: "" },
};

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

export default function SettingsPage() {
  const [keys, setKeys] = useState<Record<string, string>>(getStoredKeys);
  const [selectedProvider, setSelectedProvider] = useState("anthropic");
  const [keyInput, setKeyInput] = useState("");
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    // Also try to save to server if logged in
    saveStoredKeys(keys);
  }, [keys]);

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    const next = { ...keys, [selectedProvider]: key };
    setKeys(next);
    setKeyInput("");
    setMessage({ type: "ok", text: `${PROVIDERS[selectedProvider]?.name || selectedProvider} key saved.` });
  }

  function handleDelete(provider: string) {
    const next = { ...keys };
    delete next[provider];
    setKeys(next);
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
          Configure API keys for AI providers. The AI assistant uses Anthropic by default.
          Keys are stored in your browser locally.
        </p>

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
    </div>
  );
}
