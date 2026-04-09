export default function HelpPage() {
  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div>
        <h1>Help</h1>
        <p style={{ color: "var(--color-text-secondary)", maxWidth: 820 }}>
          This page collects the built-in workflows that are easy to miss: sky search, ADQL,
          pipeline execution, AI handoff, and notebook export.
        </p>
      </div>

      <section className="settings-card">
        <h3>Data Browser</h3>
        <p>Use Quick Search for object names or coordinates. If MAST/JWST name resolution is flaky, add SIMBAD or provide RA/Dec directly.</p>
        <p>Use “Sky View” to open Aladin Lite, “Fetch FITS” to save archive data, and “Open in Pipeline” to build a starter workflow from selected rows.</p>
      </section>

      <section className="settings-card">
        <h3>ADQL</h3>
        <p>Run a TAP query, then export CSV or Notebook, or send the query and a result sample into the AI Assistant. The Python sandbox can also access the latest ADQL result via <code>get_adql_results()</code>.</p>
      </section>

      <section className="settings-card">
        <h3>Pipeline</h3>
        <p>Build a graph visually, run it asynchronously, and export completed runs as CSV, VOTable, or PDF. “Ask AI About This Pipeline” opens the assistant with the current graph context.</p>
      </section>

      <section className="settings-card">
        <h3>AI Assistant</h3>
        <p>The sandbox already includes NumPy, SciPy, Astropy, plotting helpers, and platform helper functions. Run <code>available_functions()</code> in Python to inspect built-in astronomy utilities.</p>
        <p>Python state is isolated per chat session, so multi-step analysis can span multiple code blocks without leaking variables across chats.</p>
      </section>

      <section className="settings-card">
        <h3>Exports</h3>
        <p>Search results and chats can be exported as Jupyter notebooks. Chat export converts executed <code>run_python</code> actions into code cells, while markdown export preserves the conversation transcript.</p>
      </section>
    </div>
  );
}
