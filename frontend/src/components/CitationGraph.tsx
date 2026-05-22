/**
 * CitationGraph — SVG-based citation network visualization.
 *
 * Uses a simple force-directed layout with requestAnimationFrame (no D3 dependency).
 * Nodes are circles sized by log(citation_count + 1), colored by role:
 *   - Blue: original (seed) papers
 *   - Gold: top 10 % by citation count
 *   - Gray: other discovered papers
 * Hover for tooltip, click to open the ADS abstract page.
 */

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  fetchCitationGraph,
  type CitationGraphNode,
  type CitationGraphEdge,
} from "../api/client";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface SimNode {
  id: string;
  title: string;
  authors: string;
  year: number;
  citations: number;
  inOriginalSet: boolean;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
}

interface SimEdge {
  source: string;
  target: string;
}

interface Props {
  /** Seed bibcodes to build the graph from. */
  bibcodes: string[];
  /** BFS depth (default 1). */
  depth?: number;
  /** Height of the SVG in pixels. */
  height?: number;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function nodeRadius(citations: number): number {
  return 4 + Math.log(citations + 1) * 2.5;
}

function nodeColor(node: SimNode, citationThreshold: number): string {
  if (node.inOriginalSet) return "#3b82f6"; // blue
  if (node.citations >= citationThreshold && citationThreshold > 0) return "#eab308"; // gold
  return "#9ca3af"; // gray
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function CitationGraph({ bibcodes, depth = 1, height = 500 }: Props) {
  const [nodes, setNodes] = useState<SimNode[]>([]);
  const [edges, setEdges] = useState<SimEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [hovered, setHovered] = useState<SimNode | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });

  const svgRef = useRef<SVGSVGElement>(null);
  const frameRef = useRef(0);
  const nodesRef = useRef<SimNode[]>([]);
  const iterRef = useRef(0);

  // Compute the top-10 % citation threshold
  const citationThreshold = useMemo(() => {
    if (nodes.length === 0) return 0;
    const sorted = nodes.map((n) => n.citations).sort((a, b) => b - a);
    const idx = Math.max(0, Math.floor(sorted.length * 0.1) - 1);
    return sorted[idx];
  }, [nodes]);

  /* ---- Fetch graph data ---- */
  useEffect(() => {
    if (bibcodes.length === 0) return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setInfo(null);

    fetchCitationGraph(bibcodes, depth)
      .then((resp) => {
        if (cancelled) return;
        if (resp.info) setInfo(resp.info);

        const width = svgRef.current?.clientWidth ?? 700;
        const h = height;

        const simNodes: SimNode[] = resp.nodes.map(
          (n: CitationGraphNode) => ({
            id: n.id,
            title: n.title,
            authors: n.authors,
            year: n.year,
            citations: n.citations,
            inOriginalSet: n.in_original_set,
            x: width / 2 + (Math.random() - 0.5) * width * 0.6,
            y: h / 2 + (Math.random() - 0.5) * h * 0.6,
            vx: 0,
            vy: 0,
            radius: nodeRadius(n.citations),
          })
        );

        const simEdges: SimEdge[] = resp.edges.map((e: CitationGraphEdge) => ({
          source: e.source,
          target: e.target,
        }));

        nodesRef.current = simNodes;
        iterRef.current = 0;
        setNodes(simNodes);
        setEdges(simEdges);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [bibcodes, depth, height]);

  /* ---- Force simulation via rAF (Q2: ref-trampoline avoids stale closure
       + static physics mutation is annotated with eslint-disable to signal intent) ---- */
  const tickRef = useRef<() => void>(() => {});
  tickRef.current = () => {
    const ns = nodesRef.current;
    if (ns.length === 0 || iterRef.current > 300) return;
    iterRef.current++;

    const width = svgRef.current?.clientWidth ?? 700;
    const h = height;
    const cx = width / 2;
    const cy = h / 2;
    const alpha = Math.max(0.001, 1 - iterRef.current / 300);

    // Build lookup
    const idx = new Map<string, number>();
    ns.forEach((n, i) => idx.set(n.id, i));

    // Force-directed physics simulation — nodes are mutated in place by
    // design (vx/vy/x/y are simulation state that must reflect the latest values
    // on every frame). This is a physics sim pattern, not a React immutability
    // violation. ESLint's react-hooks/immutability rule flags all mutations on
    // sub-objects of ref.current, so the entire physics block is disabled.
    /* eslint-disable react-hooks/immutability */
    // Center gravity
    for (const n of ns) {
      n.vx += (cx - n.x) * 0.005 * alpha;
      n.vy += (cy - n.y) * 0.005 * alpha;
    }

    // Repulsion (charge)
    for (let i = 0; i < ns.length; i++) {
      for (let j = i + 1; j < ns.length; j++) {
        const dx = ns[j].x - ns[i].x;
        const dy = ns[j].y - ns[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (200 * alpha) / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        ns[i].vx -= fx;
        ns[i].vy -= fy;
        ns[j].vx += fx;
        ns[j].vy += fy;
      }
    }

    // Edge spring force
    for (const e of edges) {
      const si = idx.get(e.source);
      const ti = idx.get(e.target);
      if (si === undefined || ti === undefined) continue;
      const s = ns[si];
      const t = ns[ti];
      const dx = t.x - s.x;
      const dy = t.y - s.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const desired = 80;
      const force = (dist - desired) * 0.02 * alpha;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      s.vx += fx;
      s.vy += fy;
      t.vx -= fx;
      t.vy -= fy;
    }

    // Apply velocity with damping
    for (const n of ns) {
      n.vx *= 0.6;
      n.vy *= 0.6;
      n.x += n.vx;
      n.y += n.vy;
      // Keep in bounds
      n.x = Math.max(n.radius, Math.min(width - n.radius, n.x));
      n.y = Math.max(n.radius, Math.min(h - n.radius, n.y));
    }

    /* eslint-enable react-hooks/immutability */

    setNodes([...ns]);
    // Ref-trampoline: tickRef.current self-references to avoid the useCallback
    // stale closure problem (ESLint "tick accessed before declared").
    // tickRef is reassigned to the latest closure on every render, so the RAF
    // callback always reads the up-to-date version via tickRef.current.
    frameRef.current = requestAnimationFrame(() => tickRef.current());
  };

  useEffect(() => {
    if (nodesRef.current.length > 0) {
      iterRef.current = 0;
      frameRef.current = requestAnimationFrame(() => tickRef.current());
    }
    return () => cancelAnimationFrame(frameRef.current);
  }, [edges, height]);

  /* ---- Zoom handler ---- */
  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      setScale((s) => Math.max(0.2, Math.min(5, s * factor)));
    },
    []
  );

  /* ---- Pan via mouse drag ---- */
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      dragging.current = true;
      dragStart.current = { x: e.clientX - translate.x, y: e.clientY - translate.y };
    },
    [translate]
  );

  const onMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (dragging.current) {
        setTranslate({
          x: e.clientX - dragStart.current.x,
          y: e.clientY - dragStart.current.y,
        });
      }
    },
    []
  );

  const onMouseUp = useCallback(() => {
    dragging.current = false;
  }, []);

  /* ---- Render ---- */
  if (loading) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--color-text-secondary)" }}>
        Building citation graph...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 20, color: "var(--color-error, #ef4444)" }}>
        Error loading citation graph: {error}
      </div>
    );
  }

  if (info && nodes.length === 0) {
    return (
      <div style={{ padding: 20, color: "var(--color-text-secondary)" }}>
        {info}
      </div>
    );
  }

  if (nodes.length === 0) {
    return null;
  }

  // Build a lookup for edge rendering
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  return (
    <div style={{ position: "relative", border: "1px solid var(--color-border, #333)", borderRadius: 8, overflow: "hidden" }}>
      {/* Legend */}
      <div
        style={{
          position: "absolute",
          top: 8,
          left: 8,
          zIndex: 2,
          background: "rgba(0,0,0,0.7)",
          padding: "6px 10px",
          borderRadius: 6,
          fontSize: "0.7rem",
          color: "#ccc",
          display: "flex",
          gap: 12,
        }}
      >
        <span><span style={{ color: "#3b82f6" }}>&#9679;</span> Seed papers</span>
        <span><span style={{ color: "#eab308" }}>&#9679;</span> Top 10% cited</span>
        <span><span style={{ color: "#9ca3af" }}>&#9679;</span> Discovered</span>
        <span style={{ marginLeft: 8, color: "#888" }}>
          {nodes.length} nodes, {edges.length} edges
        </span>
      </div>

      {/* SVG canvas */}
      <svg
        ref={svgRef}
        width="100%"
        height={height}
        style={{ background: "var(--color-bg-secondary, #111)", cursor: dragging.current ? "grabbing" : "grab" }}
        onWheel={handleWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <g transform={`translate(${translate.x},${translate.y}) scale(${scale})`}>
          {/* Edges */}
          {edges.map((e, i) => {
            const s = nodeMap.get(e.source);
            const t = nodeMap.get(e.target);
            if (!s || !t) return null;
            return (
              <line
                key={`e-${i}`}
                x1={s.x}
                y1={s.y}
                x2={t.x}
                y2={t.y}
                stroke="rgba(255,255,255,0.12)"
                strokeWidth={0.5}
              />
            );
          })}

          {/* Nodes */}
          {nodes.map((n) => (
            <circle
              key={n.id}
              cx={n.x}
              cy={n.y}
              r={n.radius}
              fill={nodeColor(n, citationThreshold)}
              fillOpacity={0.85}
              stroke={hovered?.id === n.id ? "#fff" : "none"}
              strokeWidth={1.5}
              style={{ cursor: "pointer" }}
              onMouseEnter={(e) => {
                setHovered(n);
                setMousePos({ x: e.clientX, y: e.clientY });
              }}
              onMouseMove={(e) => setMousePos({ x: e.clientX, y: e.clientY })}
              onMouseLeave={() => setHovered(null)}
              onClick={() =>
                window.open(`https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(n.id)}`, "_blank")
              }
              onTouchEnd={(e) => { e.preventDefault(); window.open(`https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(n.id)}`, "_blank"); }}
            />
          ))}
        </g>
      </svg>

      {/* Tooltip */}
      {hovered && (
        <div
          style={{
            position: "fixed",
            left: mousePos.x + 14,
            top: mousePos.y - 10,
            zIndex: 9999,
            background: "rgba(20,20,30,0.95)",
            border: "1px solid rgba(255,255,255,0.15)",
            borderRadius: 6,
            padding: "8px 12px",
            maxWidth: 320,
            fontSize: "0.75rem",
            color: "#e5e5e5",
            pointerEvents: "none",
            lineHeight: 1.4,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 2 }}>{hovered.title}</div>
          <div style={{ color: "#aaa" }}>
            {hovered.authors} ({hovered.year})
          </div>
          <div style={{ color: "#888", marginTop: 2 }}>
            Citations: {hovered.citations} &middot; {hovered.id}
          </div>
        </div>
      )}
    </div>
  );
}
