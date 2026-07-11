// Self-contained chat panels: the honest-abstention card and the
// first-run API key prompt.
// Moved verbatim from ChatPage.tsx (behavior-preserving split).
import { useState } from "react";
import { saveApiKey, writeStoredAiProvider } from "../../api/client";
import type { DisplayMessage } from "./chatStorage";

// F3.2: Honest-abstention card.  Rendered instead of raw markdown when
// the backend emitted <tools_returned_nothing/> as the model's reply.
// This is the UI celebration of model honesty — pale-blue ✓ bubble,
// lists failed/empty tools, shows rationale and suggested next step,
// and offers a single-click retry that pre-fills the suggestion.
export function HonestAbstentionCard({
  abstention,
  onRetry,
}: {
  abstention: NonNullable<DisplayMessage["_abstention"]>;
  onRetry?: (suggestedInput: string) => void;
}) {
  const failed = (abstention.failed_tools || "").trim();
  const empty = (abstention.empty_tools || "").trim();
  const rationale = (abstention.rationale || "").trim();
  const nextStep = (abstention.suggested_next_step || "").trim();
  const reason = abstention.reason || "no_tools";

  const header: Record<string, string> = {
    empty: "Honest reply — tools returned no data",
    failed: "Honest reply — tools failed to run",
    mixed: "Honest reply — tools returned no data and some failed",
    no_tools: "Honest reply — no claims to make",
  };

  return (
    <div className="chat-abstention-card" role="note" aria-label="honest abstention">
      <div className="abstention-header">
        <span aria-hidden="true">✓</span>
        <span>{header[reason] || header.no_tools}</span>
        <span
          aria-label="What is an honest abstention?"
          title={
            "The AI saw every tool this turn return no usable data (empty or " +
            "errored).  Instead of inventing numbers to sound helpful, it " +
            "output a structured abstention tag, which the UI renders as " +
            "this card.  This is the model's expected behaviour when it " +
            "has no data — not a bug."
          }
          style={{
            marginLeft: "auto",
            fontSize: "0.8rem",
            opacity: 0.6,
            cursor: "help",
            fontWeight: 400,
          }}
        >
          ⓘ
        </span>
      </div>
      {(failed || empty) && (
        <div className="abstention-tools">
          {failed && <span>Failed: <code>{failed}</code> </span>}
          {empty && <span>Empty: <code>{empty}</code></span>}
        </div>
      )}
      {rationale && <div className="abstention-rationale">{rationale}</div>}
      {nextStep && (
        <div className="abstention-next-step">
          <strong>Suggested next step:</strong> {nextStep}
        </div>
      )}
      {!rationale && !nextStep && (
        <div className="abstention-rationale">
          No numerical claims are made because no tool produced data this
          turn.  Please rephrase your question, provide target values
          explicitly, or try the suggested next step above.
        </div>
      )}
      {onRetry && nextStep && (
        <button
          className="abstention-retry"
          onClick={() => onRetry(nextStep)}
          title="Pre-fill the input with the suggested next step"
        >
          Try it
        </button>
      )}
    </div>
  );
}

export function ApiKeyPrompt({ onSaved }: { onSaved: () => void }) {
  const [keyInput, setKeyInput] = useState("");
  const [provider, setProvider] = useState<"anthropic" | "openai" | "deepseek">("anthropic");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const providerInfo: Record<string, { label: string; url: string; placeholder: string; rec?: boolean }> = {
    anthropic: { label: "Anthropic (Claude)", url: "https://console.anthropic.com/settings/keys", placeholder: "sk-ant-...", rec: true },
    openai:    { label: "OpenAI (GPT)",       url: "https://platform.openai.com/api-keys",        placeholder: "sk-..." },
    deepseek:  { label: "DeepSeek",            url: "https://platform.deepseek.com/api_keys",      placeholder: "sk-..." },
  };

  const info = providerInfo[provider];

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveApiKey(provider, key);
      writeStoredAiProvider(provider);
      setKeyInput("");
      onSaved();
    } catch (error: unknown) {
      const detail = error && typeof error === "object" && "response" in error
        ? (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
        : null;
      setSaveError(
        typeof detail === "string" && detail.trim()
          ? detail
          : error instanceof Error && error.message
            ? error.message
            : "Could not save the API key.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="chat-apikey-prompt">
      <h3>Configure API Key</h3>
      <p>To use the AI assistant, enter an API key from any supported provider.</p>
      <p className="chat-apikey-hint">The key is encrypted on the server and is never stored in browser Storage.</p>
      <div className="chat-apikey-provider-select">
        {Object.entries(providerInfo).map(([key, p]) => (
          <button
            key={key}
            type="button"
            className={`btn-small ${provider === key ? "btn-primary" : "btn-secondary"}`}
            onClick={() => { setProvider(key as "anthropic" | "openai" | "deepseek"); setKeyInput(""); }}
          >
            {p.label}{p.rec ? " ★" : ""}
          </button>
        ))}
      </div>
      <p className="chat-apikey-hint">
        Get a key at{" "}
        <a href={info.url} target="_blank" rel="noopener noreferrer">
          {new URL(info.url).hostname}
        </a>
      </p>
      <form className="chat-apikey-form" onSubmit={handleSave}>
        <input
          type="password"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder={info.placeholder}
          className="chat-apikey-input"
          autoComplete="off"
        />
        <button type="submit" className="btn-primary" disabled={!keyInput.trim() || saving}>
          {saving ? "Saving..." : "Save & Start"}
        </button>
      </form>
      {saveError && <p className="error-text" role="alert">{saveError}</p>}
    </div>
  );
}
