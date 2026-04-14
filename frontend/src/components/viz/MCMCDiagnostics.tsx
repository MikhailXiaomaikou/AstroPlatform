interface DiagnosticsProps {
  diagnostics?: {
    parameters?: Record<string, { rhat: number; ess: number; mcse: number; status: string }>;
    overall_status?: string;
  };
  parameterSummary?: Record<string, { median: number; std: number; q16: number; q84: number }>;
}

export default function MCMCDiagnostics({ diagnostics, parameterSummary }: DiagnosticsProps) {
  if (!diagnostics?.parameters && !parameterSummary) return null;

  const statusColor = (s: string) => s === "good" ? "#22c55e" : s === "marginal" ? "#f59e0b" : "#ef4444";

  return (
    <div className="mcmc-diagnostics" style={{ fontSize: "0.85rem" }}>
      <h4 style={{ margin: "0 0 8px" }}>Chain Diagnostics</h4>
      {diagnostics?.overall_status && (
        <div style={{ marginBottom: 8, color: statusColor(diagnostics.overall_status) }}>
          Overall: {diagnostics.overall_status}
        </div>
      )}
      <table className="results-table" style={{ fontSize: "0.8rem" }}>
        <thead>
          <tr><th>Parameter</th><th>Median</th><th>Std</th><th>R-hat</th><th>ESS</th><th>Status</th></tr>
        </thead>
        <tbody>
          {Object.entries(diagnostics?.parameters || {}).map(([name, d]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{parameterSummary?.[name]?.median?.toFixed(4) ?? "\u2014"}</td>
              <td>{parameterSummary?.[name]?.std?.toFixed(4) ?? "\u2014"}</td>
              <td>{d.rhat?.toFixed(4)}</td>
              <td>{d.ess?.toFixed(0)}</td>
              <td style={{ color: statusColor(d.status) }}>{d.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
