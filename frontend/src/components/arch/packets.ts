// Turns real gateway events into motion on the graph. Every packet is caused by an event from
// /ws/events (or the replay fixture); with no events, nothing moves.
//
// Edges and nodes register imperative handles here, so packets and glows animate through the
// DOM directly and a traffic burst never re-renders the React tree.
import type { GatewayEvent } from "../../lib/types";
import { nodeFor, type View } from "./layout";

export type PacketKind = "normal" | "cached" | "hedge" | "rejected" | "loser" | "probe";

export interface EdgeHandle {
  launch: (kind: PacketKind, durationMs: number) => void;
}
export interface NodeHandle {
  set: (attr: "active" | "flash" | "dim" | "badge", value: string | null) => void;
}

const edges = new Map<string, EdgeHandle>();
const nodes = new Map<string, NodeHandle>();
export const registry = {
  edge(id: string, h: EdgeHandle) {
    edges.set(id, h);
    return () => {
      if (edges.get(id) === h) edges.delete(id);
    };
  },
  node(id: string, h: NodeHandle) {
    nodes.set(id, h);
    return () => {
      if (nodes.get(id) === h) nodes.delete(id);
    };
  },
};

interface Flight {
  last: string;
  kind: PacketKind;
  lastMs: number;
  cache?: string;
  coalesced?: boolean;
  backend?: string;
  done?: boolean;
  hedged?: boolean;
}

const SKIPPED_ON_HIT = ["coalescer", "router", "hedger"];

export function duration(ms: number | undefined, slow = 1): number {
  // A stage that took longer sends a slower packet: slow stages visibly crawl.
  return Math.min(3200, 420 + (ms ?? 0) * 0.9) * slow;
}

export class PacketDriver {
  private flights = new Map<string, Flight>();
  private timers = new Set<number>();
  view: View;
  slow: number;
  /** Optional: only animate this request (trace replay / follow mode). */
  only: string | null = null;
  onFocus?: (nodeId: string, requestId: string) => void;

  constructor(view: View, slow = 1) {
    this.view = view;
    this.slow = slow;
  }

  private send(from: string, to: string, kind: PacketKind, ms: number) {
    const a = nodeFor(this.view, from);
    const b = nodeFor(this.view, to);
    if (a === b) return;
    edges.get(`${a}->${b}`)?.launch(kind, duration(ms, this.slow));
  }

  private node(stage: string, attr: "active" | "flash" | "dim" | "badge", value: string | null, clearAfter?: number) {
    const id = nodeFor(this.view, stage);
    const h = nodes.get(id);
    if (!h) return;
    h.set(attr, value);
    if (clearAfter) {
      const t = window.setTimeout(() => {
        h.set(attr, null);
        this.timers.delete(t);
      }, clearAfter * this.slow);
      this.timers.add(t);
    }
  }

  handle(e: GatewayEvent) {
    const rid = e.request_id;
    if (e.type === "breaker.state") {
      const backend = `backend:${e.backend as string}`;
      this.node(backend, "flash", e.state === "open" ? "red" : e.state === "closed" ? "cyan" : "amber", 1200);
      return;
    }
    if (!rid || (this.only && rid !== this.only)) return;
    let f = this.flights.get(rid);
    if (e.type === "request.start") {
      f = { last: "client", kind: "normal", lastMs: 0 };
      this.flights.set(rid, f);
      this.node("client", "flash", "cyan", 500);
      return;
    }
    if (!f) return; // joined mid-request: wait for the next one rather than guess its path
    const stage = e.stage as string | undefined;
    switch (e.type) {
      case "stage.enter": {
        if (!stage || stage === "ledger_queue") return;
        if (stage === "embedder") {
          this.node("embedder", "active", "true");
          return;
        }
        if (stage === "relay") {
          const from = f.cache ? `${f.cache}_cache` : f.coalesced ? "coalescer" : f.backend ? `backend:${f.backend}` : f.last;
          this.send(from, "relay", f.kind, f.lastMs);
          f.last = "relay";
        } else {
          this.send(f.last, stage, f.kind, f.lastMs);
          f.last = stage;
        }
        this.node(stage, "active", "true");
        this.onFocus?.(nodeFor(this.view, stage), rid);
        return;
      }
      case "stage.exit": {
        if (!stage) return;
        this.node(stage, "active", null);
        f.lastMs = Number(e.ms ?? 0);
        const outcome = e.outcome as string;
        if (outcome === "rejected" || outcome === "blocked") {
          f.kind = "rejected";
          f.done = true;
          this.node(stage, "flash", "red", 1400);
        } else if (outcome === "redacted") {
          this.node(stage, "badge", "PII redacted", 1800);
        } else if (outcome === "warned") {
          this.node(stage, "flash", "amber", 1200);
        }
        return;
      }
      case "cache.hit": {
        f.kind = "cached";
        f.cache = (e.kind as string) === "semantic" ? "semantic" : "exact";
        this.node(`${f.cache}_cache`, "flash", "violet", 1400);
        for (const s of SKIPPED_ON_HIT) this.node(s, "dim", "true", 1600);
        this.dimBackends(1600);
        return;
      }
      case "coalesce.join":
        f.kind = "cached";
        f.coalesced = true;
        this.node("coalescer", "flash", "violet", 900);
        this.node("coalescer", "badge", `joined ${String(e.leader).slice(4, 10)}`, 1400);
        return;
      case "hedge.fire":
        f.kind = "hedge";
        f.hedged = true;
        this.send("router", "hedger", "hedge", 200);
        this.node("hedger", "flash", "amber", 1200);
        return;
      case "backend.try": {
        const backend = `backend:${e.backend as string}`;
        this.send(e.hedge ? "hedger" : "router", backend, e.probe ? "probe" : f.kind, f.lastMs);
        this.node(backend, "active", "true");
        return;
      }
      case "hedge.win":
        for (const loser of (e.losers as string[]) ?? []) {
          this.send(f.hedged ? "hedger" : "router", `backend:${loser}`, "loser", 600);
          this.node(`backend:${loser}`, "active", null);
          this.node(`backend:${loser}`, "badge", "lost the race", 1600);
        }
        this.node(`backend:${e.winner as string}`, "badge", "won the race", 1600);
        return;
      case "failover":
        f.kind = "hedge";
        this.node(`backend:${e.backend as string}`, "flash", "red", 1400);
        this.node(`backend:${e.backend as string}`, "active", null);
        this.node(`backend:${e.backend as string}`, "badge", String(e.reason), 1800);
        return;
      case "backend.first_token":
        f.backend = e.backend as string;
        f.lastMs = Number(e.ttft_ms ?? 0);
        f.last = `backend:${f.backend}`;
        return;
      case "request.end": {
        const status = e.status as string;
        const kind: PacketKind = status === "ok" ? (e.cache === "exact" || e.cache === "semantic" || e.cache === "coalesced" ? "cached" : f.kind === "hedge" ? "hedge" : "normal") : "rejected";
        if (f.backend) this.node(`backend:${f.backend}`, "active", null);
        this.node("relay", "active", null);
        if (f.last === "relay" || status === "ok") this.send("relay", "client", kind, 300);
        else this.send(f.last, "client", "rejected", 300);
        if (f.last === "relay") {
          this.send("relay", "ledger_queue", "normal", 200);
          const t = window.setTimeout(() => this.send("ledger_queue", "sqlite", "normal", 200), 450 * this.slow);
          this.timers.add(t);
        }
        this.flights.delete(rid);
        return;
      }
    }
  }

  private dimBackends(ms: number) {
    for (const id of nodes.keys()) if (id.startsWith("backend:")) {
      const h = nodes.get(id)!;
      h.set("dim", "true");
      const t = window.setTimeout(() => h.set("dim", null), ms);
      this.timers.add(t);
    }
  }

  dispose() {
    for (const t of this.timers) window.clearTimeout(t);
    this.timers.clear();
    this.flights.clear();
  }
}
