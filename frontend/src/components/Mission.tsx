// The Control Room's mission band: three live gauges and a heartbeat monitor. On the heartbeat,
// every real request.end event is one beat: its height is the request's latency, its colour its
// outcome. With no traffic the line runs flat, which is the truth.
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { useEffect, useRef } from "react";
import { fmtMs, fmtPct } from "../lib/format";
import { onEvent, useLive } from "../lib/live";
import { useOnScreen, useReducedMotion } from "../lib/motion";
import { token } from "./Chart";

function Gauge({ label, value, display, sub, tone = "--cyan" }: { label: string; value: number | null; display: string; sub: string; tone?: string }) {
  const reduced = useReducedMotion();
  const v = useMotionValue(0);
  const r = 52;
  const c = Math.PI * 1.5 * r; // a 270-degree arc
  const offset = useTransform(v, (x) => c * (1 - Math.max(0, Math.min(1, x))));
  useEffect(() => {
    if (value === null) return;
    const ctl = animate(v, value, reduced ? { duration: 0 } : { duration: 1.1, ease: [0.32, 0.72, 0, 1] });
    return () => ctl.stop();
  }, [value, v, reduced]);
  return (
    <figure className="flex items-center gap-4">
      <div className="relative h-[124px] w-[124px] flex-none">
        <svg viewBox="0 0 124 124" className="h-full w-full rotate-[135deg]" role="img" aria-label={`${label}: ${display}`}>
          <circle cx="62" cy="62" r={r} fill="none" strokeWidth="9" strokeLinecap="round" style={{ stroke: "var(--space-2)" }} strokeDasharray={`${c} 999`} />
          <motion.circle cx="62" cy="62" r={r} fill="none" strokeWidth="9" strokeLinecap="round" style={{ stroke: `var(${tone})`, strokeDashoffset: offset, filter: `drop-shadow(0 0 6px var(${tone}))` }} strokeDasharray={`${c} 999`} />
        </svg>
        <div className="absolute inset-0 grid place-items-center">
          <span className="font-display text-2xl font-semibold text-ink font-tabular">{display}</span>
        </div>
      </div>
      <figcaption>
        <p className="font-semibold text-ink">{label}</p>
        <p className="mt-1 max-w-[18ch] text-sm leading-snug text-ink-mute">{sub}</p>
      </figcaption>
    </figure>
  );
}

function Heartbeat() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(wrap);
  const reduced = useReducedMotion();
  const beats = useRef<{ t: number; h: number; colour: string }[]>([]);

  useEffect(() => onEvent((e) => {
    if (e.type !== "request.end") return;
    const kind = e.status !== "ok" ? "--red" : e.cache === "exact" || e.cache === "semantic" || e.cache === "coalesced" ? "--violet" : e.hedged || Number(e.failovers) > 0 ? "--amber" : "--cyan";
    const ms = Number(e.latency_ms) || 0;
    beats.current.push({ t: performance.now(), h: Math.min(1, 0.25 + Math.log10(1 + ms) / 4.2), colour: token(kind) });
    if (beats.current.length > 200) beats.current.shift();
  }), []);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !onScreen) return;
    const ctx = c.getContext("2d")!;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      c.width = c.clientWidth * dpr;
      c.height = c.clientHeight * dpr;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(c);
    const span = 20000; // the visible window: 20 s
    const grid = token("--line");
    const base = token("--cyan-deep");
    let raf = 0;
    const draw = () => {
      const now = performance.now();
      const w = c.width, h = c.height, mid = h * 0.62;
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      for (let x = 0; x < w; x += 40 * dpr) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.strokeStyle = base;
      ctx.lineWidth = 1.5 * dpr;
      ctx.stroke();
      for (const b of beats.current) {
        const x = w - ((now - b.t) / span) * w;
        if (x < -20 || x > w + 20) continue;
        const amp = b.h * h * 0.55;
        ctx.beginPath();
        ctx.moveTo(x - 14 * dpr, mid);
        ctx.lineTo(x - 6 * dpr, mid + amp * 0.18);
        ctx.lineTo(x, mid - amp);
        ctx.lineTo(x + 6 * dpr, mid + amp * 0.3);
        ctx.lineTo(x + 14 * dpr, mid);
        ctx.strokeStyle = b.colour;
        ctx.lineWidth = 2 * dpr;
        ctx.shadowColor = b.colour;
        ctx.shadowBlur = 12 * dpr;
        ctx.stroke();
        ctx.shadowBlur = 0;
        if (now - b.t < 600) {
          ctx.beginPath();
          ctx.arc(x, mid - amp, (4 + (600 - (now - b.t)) / 60) * dpr, 0, Math.PI * 2);
          ctx.fillStyle = b.colour;
          ctx.globalAlpha = (600 - (now - b.t)) / 600;
          ctx.fill();
          ctx.globalAlpha = 1;
        }
      }
      // the scan head
      const head = ctx.createLinearGradient(w - 60 * dpr, 0, w, 0);
      head.addColorStop(0, "transparent");
      head.addColorStop(1, token("--cyan-wash"));
      ctx.fillStyle = head;
      ctx.fillRect(w - 60 * dpr, 0, 60 * dpr, h);
      if (!reduced) raf = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [onScreen, reduced]);

  return (
    <div ref={wrap} className="relative">
      <canvas ref={canvas} className="block h-[124px] w-full" role="img" aria-label="Heartbeat: one beat per request in the last 20 seconds" />
      <p className="absolute left-0 top-0 text-xs text-ink-mute">heartbeat · one beat per request · last 20 s</p>
    </div>
  );
}

export default function Mission() {
  const health = useLive((s) => s.health);
  const totals = useLive((s) => s.metrics?.totals);
  const kpi = useLive((s) => s.metrics?.kpis.current);
  const comps = health ? Object.values(health.components).filter((c) => c.status !== "disabled") : [];
  const up = comps.filter((c) => c.status === "up" && c.breaker !== "open").length;
  const healthScore = comps.length ? up / comps.length : null;
  const hit = totals && totals.served ? (totals.cache_exact + totals.cache_semantic) / totals.served : null;
  const ttft = kpi?.ttft_p95_ms ?? null;
  const budget = 3000;
  return (
    <section aria-label="Mission status" className="glass glow-border is-live grid gap-6 p-6 lg:grid-cols-[auto_auto_auto_1fr] lg:items-center">
      <Gauge label="System health" value={healthScore} display={healthScore === null ? "—" : `${up}/${comps.length}`} sub="components up, with breakers closed" tone={healthScore !== null && healthScore < 1 ? "--amber" : "--cyan"} />
      <Gauge label="Answered from cache" value={hit} display={fmtPct(hit)} sub="all time, exact + semantic" tone="--violet" />
      <Gauge label="First token, p95" value={ttft === null ? 0 : Math.min(1, ttft / budget)} display={ttft === null ? "idle" : fmtMs(ttft)} sub={`last minute, against a ${budget / 1000} s budget`} tone={ttft !== null && ttft > budget ? "--red" : "--blue"} />
      <Heartbeat />
    </section>
  );
}
