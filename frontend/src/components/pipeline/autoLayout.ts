/**
 * D2: pure-stdlib layered DAG auto-layout.
 *
 * Computes x/y positions for each node by assigning levels via
 * longest-path topological sort (Sugiyama style) and stacking nodes
 * vertically within each level. No external dependencies — elkjs would
 * give prettier results for highly branched graphs but requires a
 * dependency install; this is tractable for the usual 5-30 node
 * pipelines the user actually builds.
 *
 * Usage:
 *   const laid = computeLayeredLayout(nodes, edges);
 *   setNodes(laid);
 */

import type { Edge, Node } from "reactflow";

interface LayoutOptions {
  xGap?: number;
  yGap?: number;
  xOffset?: number;
  yOffset?: number;
}

const DEFAULTS: Required<LayoutOptions> = {
  xGap: 260,
  yGap: 140,
  xOffset: 80,
  yOffset: 80,
};

/**
 * Return a new node array with {position} fields overwritten by a
 * layered left-to-right layout. Other node fields are preserved.
 */
export function computeLayeredLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {},
): Node[] {
  if (nodes.length === 0) return nodes;
  const opts = { ...DEFAULTS, ...options };

  const ids = new Set(nodes.map((n) => n.id));
  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const n of nodes) {
    incoming.set(n.id, []);
    outgoing.set(n.id, []);
  }
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    incoming.get(e.target)!.push(e.source);
    outgoing.get(e.source)!.push(e.target);
  }

  // Longest-path layering via Kahn's algorithm — each node's level is
  // 1 + max(parent levels). Cycles (shouldn't happen in a valid DAG)
  // are broken by leaving unvisited nodes at level 0.
  const level = new Map<string, number>();
  const indeg = new Map<string, number>();
  for (const n of nodes) indeg.set(n.id, (incoming.get(n.id) || []).length);

  const ready: string[] = [];
  for (const [id, d] of indeg.entries()) {
    if (d === 0) {
      level.set(id, 0);
      ready.push(id);
    }
  }

  while (ready.length > 0) {
    const id = ready.shift()!;
    const myLevel = level.get(id) ?? 0;
    for (const child of outgoing.get(id) || []) {
      const prev = level.get(child);
      const candidate = myLevel + 1;
      if (prev === undefined || candidate > prev) level.set(child, candidate);
      const d = (indeg.get(child) || 0) - 1;
      indeg.set(child, d);
      if (d === 0) ready.push(child);
    }
  }

  // Any node still without a level (cycle remnant) defaults to 0
  for (const n of nodes) if (!level.has(n.id)) level.set(n.id, 0);

  // Group by level
  const byLevel = new Map<number, string[]>();
  for (const n of nodes) {
    const lv = level.get(n.id)!;
    if (!byLevel.has(lv)) byLevel.set(lv, []);
    byLevel.get(lv)!.push(n.id);
  }
  // Stable sort within a level by original node id so the layout is
  // deterministic and user re-layouts don't flap
  for (const arr of byLevel.values()) arr.sort();

  const positions = new Map<string, { x: number; y: number }>();
  for (const [lv, idsAtLevel] of byLevel.entries()) {
    idsAtLevel.forEach((id, idx) => {
      positions.set(id, {
        x: opts.xOffset + lv * opts.xGap,
        y: opts.yOffset + idx * opts.yGap,
      });
    });
  }

  return nodes.map((n) => ({
    ...n,
    position: positions.get(n.id) ?? n.position,
  }));
}
