import { useEffect, useState } from "react";
import {
  getApiKeys,
  saveApiKey,
  deleteApiKey,
  type AllKeysResponse,
  type ProviderMeta,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";

export default function SettingsPage() {
  const { user } = useAuth();
  const [keysData, setKeysData] = useState<AllKeysResponse | null>(null);
  const [loading, setLoading] = useState(true);

  // Form state
  const [selectedProvider, setSelectedProvider] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    if (user) loadKeys();
    else setLoading(false);
  }, [user]);

  async function loadKeys() {
    setLoading(true);
    try {
      const data = await getApiKeys();
      setKeysData(data);
      // Default to first unconfigured provider
      const configured = new Set(data.keys.map((k) => k.provider));
      const first = Object.keys(data.providers).find((p) => !configured.has(p));
      setSelectedProvider(first || Object.keys(data.providers)[0] || "");
    } catch {
      // not logged in
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key || !selectedProvider) return;
    setSaving(true);
    setMessage(null);
    try {
      await saveApiKey(selectedProvider, key);
      setKeyInput("");
      await loadKeys();
      setMessage({ type: "ok", text: `${providerName(selectedProvider)} key saved.` });
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "response" in err
          ? (err as { response: { data: { detail: string } } }).response?.data?.detail
          : "Failed to save";
      setMessage({ type: "err", text: msg || "Failed to save API key" });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(provider: string) {
    const name = providerName(provider);
    if (!confirm(`Remove your ${name} API key?`)) return;
    try {
      await deleteApiKey(provider);
      await loadKeys();
      setMessage({ type: "ok", text: `${name} key removed.` });
    } catch {
      setMessage({ type: "err", text: "Failed to remove API key" });
    }
  }

  function providerName(id: string): string {
    return keysData?.providers[id]?.name || id;
  }

  if (!user) {
    return (
      <div className="settings-page">
        <h1>Settings</h1>
        <p className="empty-msg">Sign in to manage your settings.</p>
      </div>
    );
  }

  const providers: Record<string, ProviderMeta> = keysData?.providers || {};
  const configuredKeys = keysData?.keys || [];
  const configuredSet = new Set(configuredKeys.map((k) => k.provider));

  return (
    <div className="settings-page">
      <h1>Settings</h1>

      <section className="settings-section">
        <h2>AI API Keys</h2>
        <p className="settings-desc">
          Configure API keys for AI providers. The AI assistant uses Anthropic by default.
          Keys are stored on the server and only used for your own requests.
        </p>

        {loading ? (
          <p className="empty-msg">Loading...</p>
        ) : (
          <>
            {/* Configured keys */}
            {configuredKeys.length > 0 && (
              <div className="settings-keys-list">
                {configuredKeys.map((k) => (
                  <div key={k.provider} className="settings-key-row">
                    <span className="settings-key-provider">{k.name}</span>
                    <code className="settings-key-masked">{k.masked_key}</code>
                    <button className="btn-danger-sm" onClick={() => handleDelete(k.provider)}>
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Add key form */}
            <form className="settings-key-form" onSubmit={handleSave}>
              <select
                value={selectedProvider}
                onChange={(e) => setSelectedProvider(e.target.value)}
                className="settings-select"
              >
                {Object.entries(providers).map(([id, meta]) => (
                  <option key={id} value={id}>
                    {meta.name}{configuredSet.has(id) ? " (update)" : ""}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder={
                  selectedProvider && providers[selectedProvider]
                    ? `${providers[selectedProvider].prefix}...`
                    : "Enter API key"
                }
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                className="settings-input"
                autoComplete="off"
              />
              <button type="submit" className="btn-primary" disabled={saving || !keyInput.trim()}>
                {saving ? "Saving..." : configuredSet.has(selectedProvider) ? "Update" : "Save"}
              </button>
            </form>

            {message && (
              <p className={`settings-msg settings-msg-${message.type}`}>{message.text}</p>
            )}
          </>
        )}
      </section>

      <section className="settings-section">
        <h2>Account</h2>
        <div className="settings-info-row">
          <span className="settings-info-label">Email</span>
          <span className="settings-info-value">{user.email}</span>
        </div>
        <div className="settings-info-row">
          <span className="settings-info-label">Plan</span>
          <span className="tier-badge tier-beta">Beta</span>
        </div>
      </section>
    </div>
  );
}
