// The Chaos Lab's mini architecture graph: requests -> gateway -> router -> each model backend,
// drawn from /api/architecture. Backends show their real health, breaker and slot use; every
// packet is a real /ws/events event (a model call, a cache answer, a hedge, a failover, a
// rejection), so a kill or an injected fault shows up here the moment it bites. No events, no
// motion. With reduced motion the packets are skipped and only the states change.
import { useEffect, useRef, useState } from "react";
import { onEvent, useLive } from "../lib/live";
import { useReducedMotion } from "../lib/motion";
import type { ArchNode, GatewayEvent } from "../lib/types";

const W = 560;
const ROW = 64;
const X = { client: 52, gateway: 186, router: 322, backend: 470 };

type Tone = "--cyan" | "--violet" | "--amber" | "--red";
interface Packet { key: number; path: string; tone: Tone; dur: number }

const TONE_OF: Record<string, Tone> = { up: "--cyan", degraded: "--amber", down: "--red" };

function status(n: ArchNode): { tone: Tone | "--ink-mute"; note: string } {
  if (n.status === "disabled") return { tone: "--ink-mute", note: "not installed" };
  if (n.breaker?.state === "open") return { tone: "--red", note: `breaker open · ${Math.ceil(n.breaker.retry_after_s)} s` };
  if (n.breaker?.state === "half_open") return { tone: "--amber", note: "breaker probing" };
  const slots = n.slots ? ` · ${n.slots.in_flight}/${n.slots.capacity} slots` : "";
  return { tone: TONE_OF[n.status] ?? "--ink-mute", note: `${n.status}${slots}` };
}

export default function MiniGraph() {
  const arch = useLive((s) => s.architecture);
  const reduced = useReducedMotion();
  const [packets, setPackets] = useState<Packet[]>([]);
  const seq = useRef(0);
  const backends = (arch?.nodes ?? []).filter((n) => n.kind === "backend");
  const h = Math.max(3, backends.length) * ROW + 24;
  const mid = h / 2;
  const yOf = useRef<Record<string, number>>({});
  yOf.current = Object.fromEntries(backends.map((b, i) => [b.id.slice(8), 12 + ROW / 2 + i * ROW + (h - 24 - backends.length * ROW) / 2]));

  useEffect(() => {
    if (reduced) return;
    const fire = (path: string, tone: Tone, dur = 0.55) => {
      const key = ++seq.current;
      setPackets((p) => [...p.slice(-24), { key, path, tone, dur }]);
      window.setTimeout(() => setPackets((p) => p.filter((x) => x.key !== key)), dur * 1000 + 80);
    };
    const toBackend = (b: unknown) => {
      const y = yOf.current[String(b)];
      return y === undefined ? null : `M${X.router + 50},${mid} C${X.router + 90},${mid} ${X.backend - 90},${y} ${X.backend - 34},${y}`;
    };
    return onEvent((e: GatewayEvent) => {
      if (e.type === "request.start") fire(`M${X.client + 30},${mid} L${X.gateway - 40},${mid}`, "--cyan", 0.4);
      else if (e.type === "cache.hit" || e.type === "coalesce.join") fire(`M${X.gateway - 40},${mid + 10} L${X.client + 30},${mid + 10}`, "--violet", 0.4);
      else if (e.type === "stage.exit" && (e.outcome === "rejected" || e.outcome === "blocked")) fire(`M${X.gateway - 40},${mid + 10} L${X.client + 30},${mid + 10}`, "--red", 0.4);
      else if (e.type === "backend.try") {
        if (!e.hedge) fire(`M${X.gateway + 42},${mid} L${X.router - 50},${mid}`, "--cyan", 0.3);
        const p = toBackend(e.backend);
        if (p) fire(p, e.hedge ? "--amber" : "--cyan", 0.6);
      } else if (e.type === "hedge.fire") {
        const p = toBackend(e.hedge);
        if (p) fire(p, "--amber", 0.6);
      } else if (e.type === "failover") {
        const p = toBackend(e.backend);
        if (p) fire(p, "--red", 0.5);
      }
    });
  }, [reduced, mid]);

  if (!arch) return null;
  const box = (x: number, y: number, label: string, sub: string, tone: string, wide = 76) => (
    <g key={label} transform={`translate(${x - wide / 2},${y - 20})`}>
      <rect width={wide} height={40} rx={10} fill="var(--space-1)" stroke={`var(${tone})`} strokeOpacity={0.7} />
      <text x={wide / 2} y={17} textAnchor="middle" fill="var(--ink)" fontSize={12} fontWeight={600}>{label}</text>
      <text x={wide / 2} y={31} textAnchor="middle" fill="var(--ink-mute)" fontSize={10}>{sub}</text>
    </g>
  );

  return (
    <section aria-labelledby="mini" className="glass p-5">
      <h2 id="mini" className="font-display text-xl font-semibold">Live path</h2>
      <svg viewBox={`0 0 ${W} ${h}`} className="mt-3 w-full" role="img"
        aria-label={`Requests flow through the gateway and router to ${backends.map((b) => `${b.label}: ${status(b).note}`).join("; ")}`}>
        <g stroke="var(--glass-edge-hi)" strokeWidth={1.5} fill="none">
          <path d={`M${X.client + 30},${mid} L${X.gateway - 40},${mid}`} />
          <path d={`M${X.gateway + 42},${mid} L${X.router - 50},${mid}`} />
          {backends.map((b) => {
            const y = yOf.current[b.id.slice(8)];
            const open = b.breaker?.state === "open";
            return <path key={b.id} d={`M${X.router + 50},${mid} C${X.router + 90},${mid} ${X.backend - 90},${y} ${X.backend - 34},${y}`}
              stroke={open ? "var(--red)" : undefined} strokeDasharray={open ? "4 6" : undefined} strokeOpacity={open ? 0.8 : undefined} />;
          })}
        </g>
        {box(X.client, mid, "Requests", "clients", "--glass-edge-hi", 64)}
        {box(X.gateway, mid, "Gateway", "guard · cache", "--cyan", 84)}
        {box(X.router, mid, "Router", "slots · breakers", "--cyan", 100)}
        {backends.map((b) => {
          const s = status(b);
          return box(X.backend + 20, yOf.current[b.id.slice(8)], b.label, s.note, s.tone, 124);
        })}
        {packets.map((p) => (
          <circle key={p.key} r={4.5} fill={`var(${p.tone})`} style={{ filter: `drop-shadow(0 0 5px var(${p.tone}))` }}>
            <animateMotion dur={`${p.dur}s`} path={p.path} fill="freeze" />
          </circle>
        ))}
      </svg>
      <p className="mt-2 text-xs text-ink-mute">Each dot is a real event: cyan a model call, violet a cache answer, amber a hedge, red a failure or rejection.</p>
    </section>
  );
}
