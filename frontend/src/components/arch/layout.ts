// Where nodes sit. Positions for the known pipeline are fixed so the request path reads as a
// loop (in along the top, back through the reflexes, down to the models, out through the relay).
// Backends and anything else the API adds are placed automatically, so a new YAML backend shows
// up with no frontend edit.
import type { ArchEdge, ArchNode } from "../../lib/types";

export type View = "overview" | "detailed";

const COL = 225;
const ROW = 170;

// In along the top (row 0), back along row 1 through memory, safety and the reflexes, down to
// the models (row 2, columns 1-5), out through the relay (column 0). Side systems sit on row 3.
const DETAILED: Record<string, [number, number]> = {
  client: [0, ROW],
  auth: [COL, 0],
  guardrails: [2 * COL, 0],
  ratelimit: [3 * COL, 0],
  budget: [4 * COL, 0],
  exact_cache: [5 * COL, 0],
  semantic_cache: [5 * COL, ROW],
  embedder: [6 * COL, ROW],
  coalescer: [4 * COL, ROW],
  safety: [3 * COL, ROW],
  router: [2 * COL, ROW],
  hedger: [COL, ROW],
  relay: [0, 2 * ROW],
  ledger_queue: [COL, 3 * ROW],
  sqlite: [2 * COL, 3 * ROW],
  redis: [6 * COL, 2 * ROW],
  otel: [0, 3 * ROW],
  chaos: [4 * COL, 3 * ROW],
};

export const GROUP_OF: Record<string, string> = {
  client: "Clients",
  auth: "Ingress",
  guardrails: "Ingress",
  ratelimit: "Policy",
  budget: "Policy",
  exact_cache: "Memory",
  semantic_cache: "Memory",
  embedder: "Memory",
  coalescer: "Memory",
  safety: "Reflexes",
  router: "Reflexes",
  hedger: "Reflexes",
  relay: "Delivery",
  ledger_queue: "Delivery",
  sqlite: "Delivery",
};

export const OVERVIEW_GROUPS = ["Clients", "Ingress", "Policy", "Memory", "Reflexes", "Backends", "Delivery"];
const OVERVIEW_POS: Record<string, [number, number]> = {
  Clients: [0, 160],
  Ingress: [260, 0],
  Policy: [520, 0],
  Memory: [780, 0],
  Reflexes: [780, 300],
  Backends: [520, 300],
  Delivery: [260, 300],
};

export function groupOf(id: string): string {
  if (id.startsWith("backend:")) return "Backends";
  return GROUP_OF[id] ?? "Side";
}

export function positionNodes(nodes: ArchNode[]): Record<string, [number, number]> {
  const pos: Record<string, [number, number]> = {};
  // models fill row 2 from column 1 (under the hedger) to column 5; more than five wrap to a
  // second row, so a long YAML chain never lands on the relay or another node
  const backends = nodes.filter((n) => n.kind === "backend");
  backends.forEach((n, i) => {
    const row = Math.floor(i / 5);
    pos[n.id] = [(1 + (i % 5)) * COL, (2 + row * 2.6) * ROW];
  });
  let extra = 0;
  for (const n of nodes) {
    if (pos[n.id]) continue;
    if (DETAILED[n.id]) pos[n.id] = DETAILED[n.id];
    else pos[n.id] = [(5 + extra++) * COL, 3 * ROW + (backends.length > 5 ? 2.6 * ROW : 0)];
  }
  return pos;
}

export function overviewGraph(nodes: ArchNode[]) {
  const groups = OVERVIEW_GROUPS.map((g) => {
    const members = nodes.filter((n) => groupOf(n.id) === g);
    const worst = members.some((m) => m.status === "down" || m.breaker?.state === "open")
      ? "down"
      : members.some((m) => m.status === "degraded" || m.breaker?.state === "half_open")
        ? "degraded"
        : "up";
    return { id: `group:${g}`, label: g, members, status: worst, position: OVERVIEW_POS[g] ?? [0, 0] };
  });
  const order = ["Clients", "Ingress", "Policy", "Memory", "Reflexes", "Backends", "Delivery", "Clients"];
  const edges: ArchEdge[] = [];
  for (let i = 0; i < order.length - 1; i++) {
    edges.push({ id: `group:${order[i]}->group:${order[i + 1]}`, source: `group:${order[i]}`, target: `group:${order[i + 1]}`, kind: i === order.length - 2 ? "response" : "flow", status: "ok" });
  }
  edges.push({ id: "group:Memory->group:Delivery", source: "group:Memory", target: "group:Delivery", kind: "cache", status: "ok" });
  return { groups, edges };
}

/** For a stage id, the node it is drawn as in the current view. */
export function nodeFor(view: View, stage: string): string {
  return view === "overview" ? `group:${groupOf(stage)}` : stage;
}
