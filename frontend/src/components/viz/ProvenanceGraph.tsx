interface ProvenanceNode { id: string; type: string; label: string; params?: Record<string, unknown> }
interface ProvenanceEdge { from: string; to: string }
interface ProvenanceGraphProps {
  nodes: ProvenanceNode[];
  edges: ProvenanceEdge[];
}

const TYPE_COLORS: Record<string, string> = {
  data: "#3b82f6", activity: "#22c55e", agent: "#f59e0b",
};

export default function ProvenanceGraph({ nodes, edges }: ProvenanceGraphProps) {
  if (!nodes || nodes.length === 0) return <div style={{ padding: 16 }}>No provenance data</div>;

  return (
    <div className="provenance-graph" style={{ fontSize: "0.85rem" }}>
      <h4 style={{ margin: "0 0 8px" }}>Data Lineage</h4>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {nodes.map(n => (
          <div key={n.id} style={{
            padding: "8px 12px", borderRadius: 6,
            border: `2px solid ${TYPE_COLORS[n.type] || "#888"}`,
            background: `${TYPE_COLORS[n.type] || "#888"}11`,
          }}>
            <strong>{n.label}</strong>
            <span style={{ marginLeft: 8, fontSize: "0.75rem", opacity: 0.7 }}>{n.type}</span>
          </div>
        ))}
      </div>
      {edges.length > 0 && (
        <div style={{ marginTop: 8, fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>
          {edges.length} connection(s): {edges.map(e => `${e.from} \u2192 ${e.to}`).join(", ")}
        </div>
      )}
    </div>
  );
}
