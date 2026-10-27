// The flagship: this gateway's real architecture, drawn from /api/architecture and animated only
// by real events from /ws/events. If the socket drops, nothing pulses.
import { BlurWords } from "../components/fx";
import { Controls, ReactFlow, ReactFlowProvider, useReactFlow, ViewportPortal, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AnimatePresence, motion } from "motion/react";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { groupOf, overviewGraph, positionNodes, type View } from "../components/arch/layout";
import { JOURNEY, metaFor } from "../components/arch/meta";
import { nodeTypes } from "../components/arch/nodes";
import { PacketDriver } from "../components/arch/packets";
import PulseEdge from "../components/arch/PulseEdge";
import { api, streamChat } from "../lib/api";
import { backendLabel, fmtAgo, fmtMs, fmtUsd } from "../lib/format";
import { onEvent, useLive } from "../lib/live";
import { EASE_UI, useReducedMotion } from "../lib/motion";
import type { ArchEdge, ArchNode, Architecture as Arch, GatewayEvent, HealthComponent } from "../lib/types";

const edgeTypes = { pulse: PulseEdge };
type Tab = View | "trace" | "galaxy";

const Galaxy3D = lazy(() => import("../scenes/Galaxy3D"));

function handlesFor(a: [number, number], b: [number, number]) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ["s-r", "t-l"] : ["s-l", "t-r"];
  return dy >= 0 ? ["s-b", "t-t"] : ["s-t", "t-b"];
}

function buildGraph(arch: Arch, view: View, hovered: string | null, selfcheck: Record<string, number>) {
  if (view === "overview") {
    const { groups, edges } = overviewGraph(arch.nodes);
    const pos = Object.fromEntries(groups.map((g) => [g.id, g.position])) as Record<string, [number, number]>;
    const nodes: Node[] = groups.map((g) => ({ id: g.id, type: "group", position: { x: g.position[0], y: g.position[1] }, data: { label: g.label, status: g.status, members: g.members } }));
    return { nodes, edges: toEdges(edges, pos, null) };
  }
  const pos = positionNodes(arch.nodes);
  const related = hovered ? relatedSet(hovered, arch.edges) : null;
  const nodes: Node[] = arch.nodes.map((n) => ({
    id: n.id,
    type: n.kind === "backend" ? "backend" : "stage",
    position: { x: pos[n.id][0], y: pos[n.id][1] },
    data: { node: n, dimmed: related ? !related.has(n.id) : false, selfcheckMs: selfcheck[n.id] },
  }));
  return { nodes, edges: toEdges(arch.edges, pos, related) };
}

function toEdges(edges: ArchEdge[], pos: Record<string, [number, number]>, related: Set<string> | null): Edge[] {
  return edges
    .filter((e) => pos[e.source] && pos[e.target])
    .map((e) => {
      const [sh, th] = handlesFor(pos[e.source], pos[e.target]);
      return {
        id: `${e.source}->${e.target}`,
        source: e.source,
        target: e.target,
        sourceHandle: sh,
        targetHandle: th,
        type: "pulse",
        data: { kind: e.kind, status: e.status, dimmed: related ? !(related.has(e.source) && related.has(e.target)) : false },
      };
    });
}

/** The node plus everything upstream and downstream of it. */
function relatedSet(id: string, edges: ArchEdge[]): Set<string> {
  const out = new Set([id]);
  const walk = (start: string, dir: "up" | "down") => {
    const stack = [start];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const e of edges) {
        const next = dir === "down" ? (e.source === cur ? e.target : null) : e.target === cur ? e.source : null;
        if (next && !out.has(next) && e.kind !== "response") {
          out.add(next);
          stack.push(next);
        }
      }
    }
  };
  walk(id, "up");
  walk(id, "down");
  return out;
}

// ----------------------------------------------------------------------------- health strip

const PILLS: [string, string][] = [["backend:mlx-qwen", "MLX"], ["backend:ollama-llama", "Ollama"], ["redis", "Redis"], ["jaeger", "Jaeger"], ["embedder", "Embedder"], ["backend:litellm-ollama", "LiteLLM (optional)"]];

function HealthStrip() {
  const health = useLive((s) => s.health);
  const ws = useLive((s) => s.wsConnected);
  const mode = useLive((s) => s.mode);
  const [open, setOpen] = useState<string | null>(null);
  const comps = health?.components ?? {};
  const extra = Object.keys(comps).filter((k) => k.startsWith("backend:") && !PILLS.some(([id]) => id === k));
  const pills: [string, string][] = [...PILLS, ...extra.map((k): [string, string] => [k, k.slice(8)])];
  return (
    <div className="relative flex flex-wrap items-center gap-2" role="list" aria-label="Component health">
      {pills.map(([id, label]) => {
        const c: HealthComponent | undefined = comps[id];
        const status = c ? (c.breaker === "open" ? "down" : c.status) : "unknown";
        return (
          <button key={id} type="button" role="listitem" onClick={() => setOpen(open === id ? null : id)} aria-expanded={open === id}
            className="glass flex min-h-9 items-center gap-2 px-3 py-1.5 text-sm text-ink-soft hover:text-ink">
            <span className={`status-dot s-${status}`} aria-hidden="true" />
            {label}
            <span className="text-ink-mute font-tabular">{c ? fmtMs(c.latency_ms) : "—"}</span>
          </button>
        );
      })}
      <span className="glass flex min-h-9 items-center gap-2 px-3 py-1.5 text-sm text-ink-soft">
        <span className={`status-dot ${mode === "replay" ? "s-degraded" : ws ? "s-up" : "s-down"}`} aria-hidden="true" />
        WebSocket {mode === "replay" ? "(replay)" : ws ? "connected" : "disconnected"}
      </span>
      <AnimatePresence>
        {open && comps[open] && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2, ease: EASE_UI }}
            className="glass glass-strong absolute left-0 top-full z-20 mt-2 w-[min(520px,90vw)] p-4 text-sm">
            <p className="font-semibold text-ink">{open} · raw probe</p>
            <pre className="mt-2 whitespace-pre-wrap break-words text-ink-soft">{JSON.stringify(comps[open], null, 2)}</pre>
            <p className="mt-2 text-ink-mute">checked {fmtAgo(comps[open].checked_at)}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ----------------------------------------------------------------------------- drawer

function Drawer({ node, onClose }: { node: ArchNode; onClose: () => void }) {
  const recent = useLive((s) => s.recent);
  const events = recent.filter((e) => e.stage === node.id || (node.id.startsWith("backend:") && e.backend === node.id.slice(8))).slice(-10).reverse();
  return (
    <motion.aside initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }} transition={{ type: "spring", stiffness: 260, damping: 28 }}
      className="glass glass-strong absolute bottom-3 right-3 top-3 z-20 w-[min(420px,calc(100%-24px))] overflow-y-auto overscroll-contain p-6" aria-label={`${node.label} details`}>
      <div className="flex items-start justify-between gap-4">
        <h2 className="font-display text-2xl font-semibold">{node.label}</h2>
        <button type="button" className="btn btn-quiet min-h-9 px-3" onClick={onClose} aria-label="Close details">Close</button>
      </div>
      <p className="mt-3 leading-relaxed text-ink-soft">{node.about}</p>
      <dl className="mt-5 grid grid-cols-2 gap-3 text-sm">
        {Object.entries(node.stats ?? {}).filter(([, v]) => v !== null && v !== undefined).slice(0, 10).map(([k, v]) => (
          <div key={k}>
            <dt className="text-ink-mute">{k.replace(/_/g, " ")}</dt>
            <dd className="font-semibold text-ink font-tabular">{k.endsWith("_ms") ? fmtMs(Number(v)) : String(v)}</dd>
          </div>
        ))}
        {node.breaker && (
          <div>
            <dt className="text-ink-mute">breaker</dt>
            <dd className="font-semibold text-ink">{node.breaker.state.replace("_", "-")} · {node.breaker.trips} trips</dd>
          </div>
        )}
      </dl>
      {node.config && (
        <>
          <h3 className="mt-6 font-semibold text-ink">Live config</h3>
          <pre className="mt-2 max-h-56 overflow-auto rounded-lg p-3 text-[0.78rem] text-ink-soft" style={{ background: "var(--space-1)" }}>{JSON.stringify(node.config, null, 2)}</pre>
        </>
      )}
      <h3 className="mt-6 font-semibold text-ink">Last {events.length} events</h3>
      <ul className="mt-2 space-y-1.5 text-[0.82rem]">
        {events.length === 0 && <li className="text-ink-mute">No events yet. Send a request.</li>}
        {events.map((e) => (
          <li key={e.seq} className="flex justify-between gap-3 text-ink-soft">
            <span>{e.type}{e.outcome ? ` · ${e.outcome}` : ""}{e.ms !== undefined ? ` · ${fmtMs(Number(e.ms))}` : ""}</span>
            <span className="text-ink-mute">{fmtAgo(e.ts)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-6 text-sm text-ink-mute">
        Source: <a className="text-cyan underline underline-offset-2" href={`vscode://file/${(node as ArchNode & { source_abs?: string }).source_abs ?? node.source}`}>{node.source}</a>
      </p>
    </motion.aside>
  );
}

// ----------------------------------------------------------------------------- live feed + trace replay

function LiveFeed({ onPick, picked }: { onPick: (id: string) => void; picked: string | null }) {
  const recent = useLive((s) => s.recent);
  const ends = recent.filter((e) => e.type === "request.end").slice(-12).reverse();
  return (
    <section aria-labelledby="feed-title" className="glass p-4">
      <h2 id="feed-title" className="font-semibold text-ink">Live requests</h2>
      <ul className="mt-3 space-y-1">
        {ends.length === 0 && <li className="text-sm text-ink-mute">Nothing yet. Send a test request.</li>}
        {ends.map((e) => (
          <li key={e.seq}>
            <button type="button" onClick={() => onPick(e.request_id!)} aria-pressed={picked === e.request_id}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[0.82rem] hover:bg-[var(--glass)]"
              style={picked === e.request_id ? { background: "var(--glass-strong)" } : undefined}>
              <span className={`status-dot s-${e.status === "ok" ? (e.cache === "miss" || e.cache === "bypass" ? "ok" : "cached") : "error"}`} aria-hidden="true" />
              <span className="w-24 truncate text-ink-soft">{String(e.tenant)}</span>
              <span className="flex-1 truncate text-ink-mute">{e.status !== "ok" ? String(e.http_status) : e.cache === "miss" || e.cache === "bypass" ? backendLabel(e.backend as string) : String(e.cache)}</span>
              <span className="text-ink-soft font-tabular">{fmtMs(Number(e.latency_ms))}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface Detail { request: Record<string, unknown> | null; events: GatewayEvent[]; jaeger_url: string | null }

function TraceReplay({ requestId, driver }: { requestId: string; driver: React.RefObject<PacketDriver | null> }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [step, setStep] = useState(-1);
  const [error, setError] = useState<string | null>(null);
  const mode = useLive((s) => s.mode);
  const recent = useLive((s) => s.recent);

  useEffect(() => {
    setDetail(null);
    setError(null);
    if (mode === "replay") {
      setDetail({ request: null, events: recent.filter((e) => e.request_id === requestId), jaeger_url: null });
      return;
    }
    api<Detail>(`/api/requests/${requestId}`).then(setDetail).catch((e) => setError(String(e.message)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  const play = useCallback(() => {
    if (!detail || !driver.current) return;
    const d = driver.current;
    d.only = requestId;
    d.slow = 3;
    const evs = detail.events;
    const t0 = evs[0]?.ts ?? 0;
    evs.forEach((e, i) => {
      // Slow motion: each event fires at 3x its real offset (min 220 ms apart) so steps are readable.
      window.setTimeout(() => {
        d.handle(e);
        setStep(i);
        if (i === evs.length - 1) window.setTimeout(() => { d.only = null; d.slow = 1; }, 1500);
      }, Math.max(i * 220, (e.ts - t0) * 3000));
    });
  }, [detail, driver, requestId]);

  useEffect(() => {
    if (detail?.events.length) play();
  }, [detail, play]);

  const row = detail?.request as Record<string, unknown> | null;
  const stageExits = detail?.events.filter((e) => e.type === "stage.exit") ?? [];
  return (
    <section aria-labelledby="replay-title" className="glass p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 id="replay-title" className="font-semibold text-ink">Trace replay</h2>
        <button type="button" className="btn btn-quiet min-h-9 px-3 text-sm" onClick={play} disabled={!detail}>Replay</button>
      </div>
      <p className="mt-1 truncate text-[0.8rem] text-ink-mute">{requestId}</p>
      {error && <p className="mt-2 text-sm text-red">{error}</p>}
      {row && (
        <p className="mt-2 text-sm text-ink-soft">
          {String(row.status)} · cache {String(row.cache)} · {backendLabel(row.backend as string)} · {fmtMs(Number(row.latency_ms))} · saved {fmtUsd(Number(row.saved_est_usd))} <span className="text-ink-mute">(est. cloud equivalent)</span>
        </p>
      )}
      <ol className="mt-3 space-y-1 text-[0.82rem]">
        {stageExits.map((e, i) => {
          const idx = detail!.events.indexOf(e);
          return (
            <li key={e.seq} className="flex justify-between gap-2" style={{ color: idx <= step ? "var(--ink)" : "var(--ink-faint)" }}>
              <span>{i + 1}. {String(e.stage)} <span className="text-ink-mute">{String(e.outcome)}</span></span>
              <span className="font-tabular">{fmtMs(Number(e.ms))}</span>
            </li>
          );
        })}
      </ol>
      {detail?.jaeger_url && (
        <a className="mt-3 inline-block text-sm text-cyan underline underline-offset-2" href={detail.jaeger_url} target="_blank" rel="noreferrer">Open this trace in Jaeger</a>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------------- districts + HUD

const DISTRICT_TONE: Record<string, string> = {
  Ingress: "var(--cyan)", Policy: "var(--cyan)", Memory: "var(--violet)", Reflexes: "var(--cyan)", Backends: "var(--blue)", Delivery: "var(--cyan)",
};

/** Soft glowing zones behind each subsystem's nodes, drawn in flow coordinates: one padded tile
 *  per node in its district's colour, labelled once, so districts never swallow a neighbour. */
function Districts({ nodes }: { nodes: ArchNode[] }) {
  const pos = positionNodes(nodes);
  const labelled = new Set<string>();
  const tiles = nodes
    .filter((n) => pos[n.id] && DISTRICT_TONE[groupOf(n.id)])
    .sort((a, b) => pos[a.id][1] - pos[b.id][1] || pos[a.id][0] - pos[b.id][0])
    .map((n) => {
      const g = groupOf(n.id);
      const first = !labelled.has(g);
      labelled.add(g);
      const backend = n.kind === "backend";
      return { id: n.id, g, first, x: pos[n.id][0] - 14, y: pos[n.id][1] - (first ? 26 : 14), w: (backend ? 212 : 184) + 28, h: (backend ? 104 : 64) + (first ? 40 : 28) };
    });
  return (
    <ViewportPortal>
      {tiles.map((t) => (
        <div key={t.id} className="district" style={{ transform: `translate(${t.x}px, ${t.y}px)`, width: t.w, height: t.h, ["--tone" as string]: DISTRICT_TONE[t.g] }}>
          {t.first && <span className="district-label">{t.g}</span>}
        </div>
      ))}
    </ViewportPortal>
  );
}

function Hud() {
  const recent = useLive((s) => s.recent);
  const now = Date.now() / 1000;
  const lastMin = recent.filter((e) => e.type === "request.end" && now - e.ts < 60);
  const started = new Set(recent.filter((e) => e.type === "request.start" && now - e.ts < 120).map((e) => e.request_id));
  for (const e of recent) if (e.type === "request.end") started.delete(e.request_id);
  const last = [...recent].reverse().find((e) => e.type !== "health" && e.type !== "stage.exit");
  return (
    <div className="glass glass-strong pointer-events-none absolute right-3 top-3 z-10 w-56 p-3 text-[0.78rem]" aria-label="Live graph statistics">
      <div className="grid grid-cols-2 gap-2">
        <div><p className="text-ink-mute">in flight</p><p className="gradient-num font-display text-2xl font-semibold font-tabular">{started.size}</p></div>
        <div><p className="text-ink-mute">last minute</p><p className="gradient-num font-display text-2xl font-semibold font-tabular">{lastMin.length}</p></div>
      </div>
      <p className="mt-2 truncate text-ink-soft" title={last?.type}>{last ? `${last.type}${last.stage ? ` · ${String(last.stage)}` : last.backend ? ` · ${String(last.backend)}` : ""}` : "waiting for events"}</p>
    </div>
  );
}

// ----------------------------------------------------------------------------- journey strip + legend

/** The six phases of a request, left to right. Each lights up when a real request passes
 *  through it, and counts how many did in the last minute. */
function Journey() {
  const recent = useLive((s) => s.recent);
  const refs = useRef<Record<string, HTMLLIElement | null>>({});
  useEffect(() => onEvent((e) => {
    const group = e.type === "backend.try" ? "Backends" : e.type === "stage.enter" ? groupOf(String(e.stage)) : null;
    const el = group ? refs.current[group] : null;
    if (!el) return;
    el.dataset.hot = "true";
    window.setTimeout(() => { delete el.dataset.hot; }, 650);
  }), []);
  const now = Date.now() / 1000;
  const counts: Record<string, Set<string>> = {};
  for (const e of recent) {
    if (now - e.ts > 60 || !e.request_id) continue;
    const g = e.type === "backend.try" ? "Backends" : e.type === "stage.enter" ? groupOf(String(e.stage)) : null;
    if (g) (counts[g] ??= new Set()).add(e.request_id);
  }
  return (
    <ol className="glass grid grid-cols-2 gap-x-2 gap-y-3 p-4 sm:grid-cols-3 lg:grid-cols-6" aria-label="How a request travels through the gateway">
      {JOURNEY.map((step, i) => {
        const Icon = metaFor(step.stages[0] ?? "backend").icon;
        return (
          <li key={step.group} ref={(el) => { refs.current[step.group] = el; }} className="journey-step relative flex items-start gap-3 pr-3">
            <span className="planet mt-0.5" aria-hidden="true"><Icon size={14} strokeWidth={2.2} /></span>
            <div className="min-w-0">
              <p className="font-display text-lg font-semibold leading-none text-ink">{step.title}</p>
              <p className="mt-1 text-[0.78rem] leading-snug text-ink-mute">{step.explains}</p>
              <p className="mt-1 text-[0.74rem] text-ink-soft font-tabular">{counts[step.group]?.size ?? 0} in the last minute</p>
            </div>
            {i < JOURNEY.length - 1 && <span aria-hidden="true" className="journey-arrow hidden lg:block" />}
          </li>
        );
      })}
    </ol>
  );
}

function Legend() {
  return (
    <section aria-labelledby="legend-title" className="glass p-4 text-sm">
      <h2 id="legend-title" className="font-semibold text-ink">How to read the map</h2>
      <ul className="mt-3 space-y-2 text-ink-soft">
        <li className="flex items-center gap-2"><span className="planet h-4 w-4" aria-hidden="true" /> healthy part of the gateway</li>
        <li className="flex items-center gap-2"><span className="planet planet-degraded h-4 w-4" aria-hidden="true" /> degraded, or a breaker probing</li>
        <li className="flex items-center gap-2"><span className="planet planet-down h-4 w-4" aria-hidden="true" /> down, or its breaker is open</li>
      </ul>
      <p className="mt-3 text-ink-mute">A moving light is one real request:</p>
      <ul className="mt-2 grid grid-cols-2 gap-1.5 text-ink-soft">
        <li className="flex items-center gap-2"><span className="status-dot s-live" aria-hidden="true" />answered by a model</li>
        <li className="flex items-center gap-2"><span className="status-dot s-cached" aria-hidden="true" />from cache or shared</li>
        <li className="flex items-center gap-2"><span className="status-dot s-degraded" aria-hidden="true" />raced or rerouted</li>
        <li className="flex items-center gap-2"><span className="status-dot s-down" aria-hidden="true" />rejected</li>
      </ul>
      <p className="mt-3 text-ink-mute">Slower stages move their light more slowly. Hover a node to see its path; click it for its live settings.</p>
    </section>
  );
}

// ----------------------------------------------------------------------------- export

function exportSvg(arch: Arch): string {
  const pos = positionNodes(arch.nodes);
  const w = 1700;
  const h = 860;
  const cx = (id: string) => pos[id][0] + 110;
  const cy = (id: string) => pos[id][1] + 40;
  const edges = arch.edges.filter((e) => pos[e.source] && pos[e.target]).map((e) =>
    `<line x1="${cx(e.source)}" y1="${cy(e.source)}" x2="${cx(e.target)}" y2="${cy(e.target)}" stroke="${e.status === "broken" ? "#ff6b7a" : "#3a4570"}" stroke-width="1.5"${e.kind !== "flow" ? ' stroke-dasharray="5 6"' : ""}/>`).join("");
  const colour = (s: string) => (s === "down" ? "#ff6b7a" : s === "degraded" ? "#ffc25c" : s === "up" ? "#74d7f7" : "#5b6690");
  const nodes = arch.nodes.map((n) =>
    `<g><rect x="${pos[n.id][0]}" y="${pos[n.id][1]}" width="220" height="80" rx="14" fill="#121836" stroke="${colour(n.breaker?.state === "open" ? "down" : n.status)}"/><text x="${pos[n.id][0] + 16}" y="${pos[n.id][1] + 32}" fill="#eaf0ff" font-family="Sora, sans-serif" font-size="16" font-weight="600">${n.label}</text><text x="${pos[n.id][0] + 16}" y="${pos[n.id][1] + 56}" fill="#8a95bf" font-family="Sora, sans-serif" font-size="12">${n.status}${n.breaker ? ` · breaker ${n.breaker.state}` : ""}</text></g>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="-40 -40 ${w} ${h}"><rect x="-40" y="-40" width="${w}" height="${h}" fill="#04050c"/>${edges}${nodes}</svg>`;
}

function download(name: string, blob: Blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

async function exportPng(svg: string) {
  const img = new Image();
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = img.width * 2;
  canvas.height = img.height * 2;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(2, 2);
  ctx.drawImage(img, 0, 0);
  canvas.toBlob((b) => b && download("aegis-architecture.png", b), "image/png");
}

// ----------------------------------------------------------------------------- page

function Graph({ tab, follow, setFollow, onSelect, selfcheck, driver }: {
  tab: Tab; follow: boolean; setFollow: (v: boolean) => void; onSelect: (n: ArchNode | null) => void;
  selfcheck: Record<string, number>; driver: React.RefObject<PacketDriver | null>;
}) {
  const arch = useLive((s) => s.architecture);
  const ws = useLive((s) => s.wsConnected);
  const mode = useLive((s) => s.mode);
  const [hovered, setHovered] = useState<string | null>(null);
  const view: View = tab === "overview" ? "overview" : "detailed";
  const pressed = useRef<string | null>(null);
  const rf = useReactFlow();
  const reduced = useReducedMotion();
  const followRef = useRef(follow);
  followRef.current = follow;

  const graph = useMemo(() => (arch ? buildGraph(arch, view, hovered, selfcheck) : { nodes: [], edges: [] }), [arch, view, hovered, selfcheck]);

  useEffect(() => {
    const d = new PacketDriver(view);
    d.onFocus = (nodeId) => {
      if (!followRef.current) return;
      const n = rf.getNode(nodeId);
      if (n) void rf.setCenter(n.position.x + 110, n.position.y + 40, { zoom: 1.15, duration: reduced ? 0 : 450 });
    };
    driver.current = d;
    const off = onEvent((e) => {
      if (!d.only) d.handle(e);
    });
    return () => {
      off();
      d.dispose();
    };
  }, [view, rf, driver, reduced]);

  const hasArch = arch !== null;
  useEffect(() => {
    const t = window.setTimeout(() => rf.fitView({ padding: 0.05, duration: reduced ? 0 : 400 }), 320);
    return () => window.clearTimeout(t);
  }, [view, rf, reduced, hasArch]);

  return (
    <div className="relative h-full w-full"
      onPointerDownCapture={(e) => { pressed.current = (e.target as Element).closest(".react-flow__node")?.getAttribute("data-id") ?? null; }}>
      <ReactFlow
        nodes={graph.nodes}
        edges={graph.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.05 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.3}
        maxZoom={2}
        onNodeMouseEnter={(_, n) => view === "detailed" && setHovered(n.id)}
        onNodeMouseLeave={() => setHovered(null)}
        onNodeClick={(_, n) => onSelect(view === "detailed" ? (n.data as { node: ArchNode }).node : null)}
        onPaneClick={() => {
          // A tap (or a click with no hover first) highlights the node between press and release,
          // which re-renders it, so the browser hands the click to the pane: honour the press.
          const id = pressed.current;
          const node = view === "detailed" && id ? arch?.nodes.find((n) => n.id === id) : undefined;
          onSelect(node ?? null);
        }}
        onMoveStart={(e) => {
          if (e && followRef.current) setFollow(false); // any manual pan cancels follow mode
        }}
        aria-label="Live architecture graph"
      >
        {view === "detailed" && arch && <Districts nodes={arch.nodes} />}
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
      {!arch && <p className="absolute inset-0 grid place-items-center text-ink-mute">Waiting for /api/architecture…</p>}
      {arch && <Hud />}
      {mode === "live" && !ws && arch && (
        <p role="status" className="glass absolute left-1/2 top-4 -translate-x-1/2 px-4 py-2 text-sm text-amber">
          Disconnected from the event stream: the graph is still until it reconnects.
        </p>
      )}
    </div>
  );
}

function SendTest() {
  const [prompt, setPrompt] = useState("In one sentence, what does an AI gateway do?");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const mode = useLive((s) => s.mode);
  const send = async () => {
    setBusy(true);
    setResult(null);
    try {
      const r = await streamChat({ model: "aegis-chat", temperature: 0, max_tokens: 80, messages: [{ role: "user", content: prompt }] }, {});
      const s = r.summary as Record<string, unknown> | null;
      setResult(r.status === 200 ? `${r.headers["x-cache"]} · ${backendLabel(r.headers["x-aegis-backend"])} · ${fmtMs(Number(s?.latency_ms))}` : `HTTP ${r.status}: ${String((r.error as { message?: string })?.message ?? "")}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="flex flex-1 flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <label htmlFor="test-prompt" className="sr-only">Test prompt</label>
      <input id="test-prompt" name="test-prompt" autoComplete="off" value={prompt} onChange={(e) => setPrompt(e.target.value)} className="glass min-h-11 min-w-0 flex-1 px-4 text-sm text-ink" />
      <button type="submit" className="btn btn-primary" disabled={busy || mode !== "live"}>{busy ? "Sending…" : "Send test request"}</button>
      {result && <span className="text-sm text-ink-soft" role="status">{result}</span>}
    </form>
  );
}

export default function Architecture() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("view");
  const tab: Tab = raw === "detailed" || raw === "trace" || raw === "overview" ? "detailed" : "galaxy";
  const [follow, setFollow] = useState(false);
  const [selected, setSelected] = useState<ArchNode | null>(null);
  const [selfcheck, setSelfcheck] = useState<Record<string, number>>({});
  const [checking, setChecking] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(params.get("request"));
  const driver = useRef<PacketDriver | null>(null);
  const arch = useLive((s) => s.architecture);
  const mode = useLive((s) => s.mode);
  const reducedMotion = useReducedMotion();

  const setTab = (t: Tab) => setParams((p) => { p.set("view", t); return p; });

  const runSelfcheck = async () => {
    setChecking("running");
    try {
      const r = await api<{ ok: boolean; stages: { stage: string; ms: number; ok: boolean }[]; generation: { backend: string; ms: number } }>("/api/selfcheck", { method: "POST" });
      const map: Record<string, number> = {};
      for (const s of r.stages) map[s.stage] = s.ms;
      if (r.generation.backend) map[`backend:${r.generation.backend}`] = r.generation.ms;
      setSelfcheck(map);
      setChecking(r.ok ? `Self-check passed in ${fmtMs(r.generation.ms)}` : "Self-check found a problem: see the timings");
      window.setTimeout(() => setSelfcheck({}), 12000);
    } catch (e) {
      setChecking(`Self-check failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="mx-auto max-w-[1600px] px-[var(--gutter)] py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl font-semibold tracking-tight md:text-6xl"><BlurWords text="The system, running live" shimmer /></h1>
          <p className="mt-2 max-w-[70ch] text-ink-soft">A live map of the gateway, drawn from <code>/api/architecture</code>. Every moving light is a real request from <code>/ws/events</code>.</p>
        </div>
      </div>
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <HealthStrip />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="View" className="glass flex p-1">
          {(["galaxy", "detailed"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} type="button" onClick={() => setTab(t)}
              className="min-h-9 rounded-[10px] px-4 text-sm capitalize transition-colors"
              style={tab === t ? { background: "var(--cyan)", color: "var(--space-0)", fontWeight: 600 } : { color: "var(--ink-soft)" }}>
              {t === "galaxy" ? "3D galaxy" : "Detailed"}
            </button>
          ))}
        </div>
        <label className="glass flex min-h-11 cursor-pointer items-center gap-2 px-3 text-sm text-ink-soft">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} className="accent-[var(--cyan)]" />
          Follow requests
        </label>
        <button type="button" className="btn btn-quiet" onClick={() => void runSelfcheck()} disabled={mode !== "live" || checking === "running"}>
          {checking === "running" ? "Checking…" : "Run self-check"}
        </button>
        <details className="relative">
          <summary className="btn btn-quiet list-none">Export</summary>
          <div className="glass glass-strong absolute right-0 z-30 mt-2 flex w-48 flex-col p-2 text-sm">
            <button type="button" className="rounded-lg px-3 py-2 text-left hover:bg-[var(--glass)]" disabled={!arch} onClick={() => arch && void exportPng(exportSvg(arch))}>PNG image</button>
            <button type="button" className="rounded-lg px-3 py-2 text-left hover:bg-[var(--glass)]" disabled={!arch} onClick={() => arch && download("aegis-architecture.svg", new Blob([exportSvg(arch)], { type: "image/svg+xml" }))}>SVG image</button>
            <button type="button" className="rounded-lg px-3 py-2 text-left hover:bg-[var(--glass)]" disabled={!arch} onClick={() => arch && download("aegis-topology.json", new Blob([JSON.stringify(arch, null, 2)], { type: "application/json" }))}>Topology JSON</button>
          </div>
        </details>
        {checking && checking !== "running" && <span role="status" className="text-sm text-ink-soft">{checking}</span>}
      </div>
      <div className="mt-4"><Journey /></div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_300px]">
        <div className="glass graph-space relative h-[min(76vh,820px)] min-h-[560px] overflow-hidden">
          {tab === "galaxy" ? (
            arch ? (
              <Suspense fallback={<p className="grid h-full place-items-center text-ink-mute">Loading the 3D galaxy…</p>}>
                <Galaxy3D nodes={arch.nodes} still={reducedMotion} />
                <p className="pointer-events-none absolute bottom-3 left-4 text-xs text-ink-mute">Drag to orbit · every comet is a real request · moons are the model backends</p>
              </Suspense>
            ) : (
              <p className="grid h-full place-items-center text-ink-mute">Waiting for /api/architecture…</p>
            )
          ) : (
            <ReactFlowProvider>
              <Graph tab={tab} follow={follow} setFollow={setFollow} onSelect={setSelected} selfcheck={selfcheck} driver={driver} />
            </ReactFlowProvider>
          )}
          <AnimatePresence>{selected && <Drawer key={selected.id} node={selected} onClose={() => setSelected(null)} />}</AnimatePresence>
        </div>
        <div className="flex flex-col gap-4">
          {picked ? <TraceReplay requestId={picked} driver={driver} /> : (
            <p className="glass p-4 text-sm text-ink-soft">Pick a request below to replay its journey in slow motion on the detailed map.</p>
          )}
          <LiveFeed picked={picked} onPick={(id) => { setPicked(id); setTab("detailed"); }} />
          <Legend />
          <Link to="/chaos" className="btn btn-quiet self-start">Break something in the Chaos Lab</Link>
        </div>
      </div>
      <div className="glass mt-4 flex flex-wrap items-center gap-3 p-3">
        <SendTest />
      </div>
    </div>
  );
}
