// Around the hero robot: two tilted orbits and three HUD callouts. Each real request.end event
// launches a particle that rides an orbit once, coloured by its outcome (cyan model, violet cache
// or coalesced, amber hedged/failed over, red rejected). The callouts show live KPIs.
import { motion } from "motion/react";
import { useEffect, useRef } from "react";
import { fmtMs, fmtPct } from "../lib/format";
import { onEvent, useLive } from "../lib/live";
import { useReducedMotion } from "../lib/motion";
import LiveNumber from "./LiveNumber";

const COLOUR: Record<string, string> = { ok: "var(--cyan-hi)", cached: "var(--violet)", hedge: "var(--cyan)", bad: "var(--red)" };

function Orbits() {
  const ring1 = useRef<SVGPathElement>(null);
  const ring2 = useRef<SVGPathElement>(null);
  const layer = useRef<SVGGElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    let n = 0;
    return onEvent((e) => {
      if (e.type !== "request.end" || reduced) return;
      const path = (n++ % 2 === 0 ? ring1 : ring2).current;
      const g = layer.current;
      if (!path || !g) return;
      const kind = e.status !== "ok" ? "bad" : e.cache === "exact" || e.cache === "semantic" || e.cache === "coalesced" ? "cached" : e.hedged || Number(e.failovers) > 0 ? "hedge" : "ok";
      const colour = COLOUR[kind];
      const len = path.getTotalLength();
      const trail: SVGCircleElement[] = [];
      for (let i = 0; i < 6; i++) {
        const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        c.setAttribute("r", String(7 - i));
        c.style.fill = colour;
        c.style.opacity = String(1 - i * 0.16);

        g.appendChild(c);
        trail.push(c);
      }
      const start = performance.now();
      const dur = 3200;
      const step = (now: number) => {
        const t = (now - start) / dur;
        trail.forEach((c, i) => {
          const pt = path.getPointAtLength(((t - i * 0.012 + 1) % 1) * len);
          c.setAttribute("cx", String(pt.x));
          c.setAttribute("cy", String(pt.y));
          if (t > 0.8) c.style.opacity = String(Math.max(0, (1 - i * 0.16) * (1 - (t - 0.8) / 0.2)));
        });
        if (t < 1) requestAnimationFrame(step);
        else trail.forEach((c) => c.remove());
      };
      requestAnimationFrame(step);
    });
  }, [reduced]);
  return (
    <svg viewBox="0 0 1000 1000" className="pointer-events-none absolute inset-[-14%] h-[128%] w-[128%]" aria-hidden="true">
      <defs>
        <linearGradient id="orbit-a" x1="0" x2="1">
          <stop offset="0%" stopColor="var(--cyan)" stopOpacity="0.05" />
          <stop offset="50%" stopColor="var(--cyan)" stopOpacity="0.55" />
          <stop offset="100%" stopColor="var(--violet)" stopOpacity="0.05" />
        </linearGradient>
        <linearGradient id="orbit-b" x1="1" x2="0">
          <stop offset="0%" stopColor="var(--magenta)" stopOpacity="0.05" />
          <stop offset="50%" stopColor="var(--violet)" stopOpacity="0.5" />
          <stop offset="100%" stopColor="var(--cyan)" stopOpacity="0.05" />
        </linearGradient>
      </defs>
      <g transform="rotate(-14 500 500)">
        <path ref={ring1} d="M 70 500 A 430 150 0 1 1 930 500 A 430 150 0 1 1 70 500" fill="none" stroke="url(#orbit-a)" strokeWidth="1.6" strokeDasharray="2 9" className="orbit" />
      </g>
      <g transform="rotate(22 500 500)">
        <path ref={ring2} d="M 120 520 A 380 120 0 1 1 880 520 A 380 120 0 1 1 120 520" fill="none" stroke="url(#orbit-b)" strokeWidth="1.4" className="orbit-rev" />
      </g>
      <g ref={layer} />
    </svg>
  );
}

function Callout({ className, label, children, delay }: { className: string; label: string; children: React.ReactNode; delay: number }) {
  const reduced = useReducedMotion();
  return (
    <motion.div className={`glass glow-border absolute px-4 py-3 ${className}`}
      initial={reduced ? false : { opacity: 0, scale: 0.85, filter: "blur(8px)" }} animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
      transition={{ delay, duration: 0.7, ease: [0.32, 0.72, 0, 1] }}>
      <p className="text-[0.72rem] text-ink-mute">{label}</p>
      <p className="font-display text-2xl font-semibold leading-tight">{children}</p>
    </motion.div>
  );
}

export default function OrbitHud() {
  const totals = useLive((s) => s.metrics?.totals);
  const kpi = useLive((s) => s.metrics?.kpis.current);
  const hit = totals && totals.served ? (totals.cache_exact + totals.cache_semantic) / totals.served : null;
  return (
    <>
      <Orbits />
      <Callout className="left-[-4%] top-[4%] hidden md:block" label="answered from cache" delay={0.6}>
        <LiveNumber value={hit} format={(n) => fmtPct(n, 1)} className="gradient-num" />
      </Callout>
      <Callout className="right-[-2%] top-[-2%] hidden md:block" label="requests served" delay={0.75}>
        <LiveNumber value={totals?.served} format={(n) => Math.round(n).toLocaleString("en-US")} className="gradient-num" />
      </Callout>
      <Callout className="bottom-[2%] right-[4%] hidden md:block" label="first token, p95 (last min)" delay={0.9}>
        <span className="gradient-num">{kpi?.ttft_p95_ms ? fmtMs(kpi.ttft_p95_ms) : "idle"}</span>
      </Callout>
    </>
  );
}
