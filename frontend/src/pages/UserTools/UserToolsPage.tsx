import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createUserTool,
  deleteUserTool,
  listUserTools,
  type UserToolDefinition,
} from "../../api/userTools";

const DEFAULT_SCHEMA = `{
  "type": "object",
  "properties": {
    "target": { "type": "string" }
  },
  "required": ["target"],
  "additionalProperties": false
}`;

const DEFAULT_STEPS = `[
  {
    "tool_name": "search_objects",
    "input": {
      "query": "{{target}}",
      "sources": ["simbad"]
    }
  }
]`;

function formatError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err || "Unknown error");
}

function parseJsonObject(text: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

function parseJsonArray(text: string, label: string): Record<string, unknown>[] {
  const parsed = JSON.parse(text);
  if (!Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON array`);
  }
  return parsed as Record<string, unknown>[];
}

export default function UserToolsPage() {
  const [tools, setTools] = useState<UserToolDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [toolId, setToolId] = useState("quick_simbad_lookup");
  const [displayName, setDisplayName] = useState("Quick SIMBAD lookup");
  const [description, setDescription] = useState("Search a target in SIMBAD using a saved one-step tool macro.");
  const [inputSchemaText, setInputSchemaText] = useState(DEFAULT_SCHEMA);
  const [stepsText, setStepsText] = useState(DEFAULT_STEPS);

  const isLoggedIn = useMemo(() => Boolean(localStorage.getItem("astro_token")), []);

  const refresh = useCallback(async () => {
    if (!isLoggedIn) {
      setTools([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setMessage(null);
    try {
      setTools(await listUserTools());
    } catch (err) {
      setMessage({ type: "err", text: formatError(err) });
    } finally {
      setLoading(false);
    }
  }, [isLoggedIn]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      const inputSchema = parseJsonObject(inputSchemaText, "Input schema");
      const steps = parseJsonArray(stepsText, "Steps");
      await createUserTool({
        tool_id: toolId,
        display_name: displayName,
        description,
        input_schema: inputSchema,
        steps: steps.map((step) => ({
          tool_name: String(step.tool_name || ""),
          input: (step.input && typeof step.input === "object" && !Array.isArray(step.input))
            ? step.input as Record<string, unknown>
            : {},
          continue_on_error: Boolean(step.continue_on_error),
        })),
      });
      setMessage({ type: "ok", text: "User tool saved. The chat assistant can now list and run it." });
      await refresh();
    } catch (err) {
      setMessage({ type: "err", text: formatError(err) });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(toolIdToDelete: string) {
    setMessage(null);
    try {
      await deleteUserTool(toolIdToDelete);
      setMessage({ type: "ok", text: `Deleted ${toolIdToDelete}.` });
      await refresh();
    } catch (err) {
      setMessage({ type: "err", text: formatError(err) });
    }
  }

  return (
    <div className="settings-page">
      <h1>User Tools</h1>
      <section className="settings-section">
        <h2>Saved Tool Macros</h2>
        <p className="settings-desc">
          Create small reusable tools from existing Standard Astro tools. This is intentionally
          not arbitrary code execution: each step still runs through the normal platform tool
          path, preserving provenance, connector gates, and validation.
        </p>
        {!isLoggedIn && (
          <p className="settings-msg settings-msg-err">
            Sign in before creating persistent user tools.
          </p>
        )}
        {message && <p className={`settings-msg settings-msg-${message.type}`}>{message.text}</p>}
        {loading ? (
          <p className="settings-desc">Loading saved tools...</p>
        ) : tools.length === 0 ? (
          <p className="settings-desc">No saved user tools yet.</p>
        ) : (
          <div className="settings-keys-list">
            {tools.map((tool) => (
              <div key={tool.tool_id} className="settings-key-row" style={{ alignItems: "flex-start" }}>
                <div>
                  <span className="settings-key-provider">{tool.display_name || tool.tool_id}</span>
                  <div className="settings-desc" style={{ margin: "2px 0 0 0" }}>
                    <code>{tool.tool_id}</code> · {tool.steps?.length || 0} step{tool.steps?.length === 1 ? "" : "s"}
                  </div>
                  <div className="settings-desc" style={{ margin: "2px 0 0 0" }}>
                    {tool.description}
                  </div>
                </div>
                <button type="button" className="btn btn-small" onClick={() => void handleDelete(tool.tool_id)}>
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="settings-section">
        <h2>Create or Update Tool</h2>
        <form className="settings-key-form" onSubmit={handleSubmit}>
          <label>
            Tool ID
            <input
              className="settings-input"
              value={toolId}
              onChange={(e) => setToolId(e.target.value)}
              placeholder="quick_simbad_lookup"
            />
          </label>
          <label>
            Display name
            <input
              className="settings-input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Quick SIMBAD lookup"
            />
          </label>
          <label>
            Description
            <input
              className="settings-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this tool does"
            />
          </label>
          <label style={{ width: "100%" }}>
            Input schema
            <textarea
              className="settings-input"
              value={inputSchemaText}
              onChange={(e) => setInputSchemaText(e.target.value)}
              rows={8}
              spellCheck={false}
              style={{ fontFamily: "var(--font-mono, monospace)" }}
            />
          </label>
          <label style={{ width: "100%" }}>
            Steps
            <textarea
              className="settings-input"
              value={stepsText}
              onChange={(e) => setStepsText(e.target.value)}
              rows={10}
              spellCheck={false}
              style={{ fontFamily: "var(--font-mono, monospace)" }}
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={saving || !isLoggedIn}>
            {saving ? "Saving..." : "Save user tool"}
          </button>
        </form>
      </section>
    </div>
  );
}
