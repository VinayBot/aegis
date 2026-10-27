import { Suspense, lazy, useRef } from "react";
import { Link } from "react-router-dom";
import Dive from "../components/Dive";
import HoverRobot from "../components/HoverRobot";
import LiveNumber from "../components/LiveNumber";
import OrbitHud from "../components/OrbitHud";
import { BlurWords, Magnetic, Reveal } from "../components/fx";
import MeetStage from "../components/MeetStage";
import { fmtInt, fmtPct, fmtUsd } from "../lib/format";
import { useLive } from "../lib/live";
import { useNearViewport, useOnScreen, useReducedMotion } from "../lib/motion";
import { useUi } from "../lib/ui";

const Starfield = lazy(() => import("../scenes/Starfield"));

function Hero() {
  const ref = useRef<HTMLElement>(null);
  const onScreen = useOnScreen(ref);
  const reduced = useReducedMotion();
  const near = useNearViewport(ref);
  return (
    <section ref={ref} aria-labelledby="hero-title" className="relative isolate overflow-hidden">
      <div className="absolute inset-0 -z-10" aria-hidden="true">
        {near && !reduced && (
          <Suspense fallback={null}>
            <Starfield active={onScreen} />
          </Suspense>
        )}
      </div>
      <div className="mx-auto grid min-h-[calc(100vh-4rem)] max-w-[1440px] items-center gap-10 px-[var(--gutter)] py-16 lg:grid-cols-[1.25fr_1fr]">
        <div>
          <h1 id="hero-title" className="font-display text-[clamp(2.5rem,5.3vw,5.6rem)] font-bold leading-[0.95] tracking-[-0.035em]">
            <BlurWords text="AEGIS guards every model call." shimmer />
          </h1>
          <Reveal delay={0.5}>
            <p className="mt-6 max-w-[34ch] text-[clamp(1.1rem,1.6vw,1.35rem)] leading-relaxed text-ink-soft">
              One local gateway in front of every AI app: it caches, coalesces, hedges, fails over and traces each request, live on this Mac.
            </p>
            <div className="mt-10 flex flex-wrap gap-3">
              <Magnetic><a href="#enter" className="btn btn-primary">Enter the system</a></Magnetic>
              <Magnetic><Link to="/control-room" className="btn btn-quiet">Open Control Room</Link></Magnetic>
            </div>
          </Reveal>
        </div>
        <div className="relative mx-auto w-full max-w-[680px]">
          <div aria-hidden="true" className="hero-aura absolute inset-[8%] rounded-full" />
          <HoverRobot priority className="relative w-full" />
          <div className="pointer-events-none absolute inset-0 z-10"><OrbitHud /></div>
        </div>
      </div>
    </section>
  );
}

function ProofStrip() {
  const totals = useLive((s) => s.metrics?.totals);
  const mode = useLive((s) => s.mode);
  const hit = totals && totals.served ? (totals.cache_exact + totals.cache_semantic) / totals.served : null;
  const items = [
    { label: "requests served", value: totals?.served, fmt: fmtInt },
    { label: "answered from cache", value: hit, fmt: (n: number) => fmtPct(n, 1) },
    { label: "saved by cache + coalescing, est. cloud equivalent", value: totals?.saved_est_usd, fmt: fmtUsd },
    { label: "breaker trips survived", value: totals?.breaker_trips, fmt: fmtInt },
  ];
  return (
    <section aria-labelledby="proof-title" className="mx-auto max-w-[1440px] px-[var(--gutter)] py-28">
      <h2 id="proof-title" className="font-display text-4xl font-semibold tracking-tight md:text-6xl">
        <BlurWords text={mode === "replay" ? "What the gateway recorded" : "What the gateway has done so far"} />
      </h2>
      <dl className="mt-12 grid gap-x-10 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((it) => (
          <div key={it.label} className="glass glow-border p-6">
            <dd className="font-display text-[clamp(2.8rem,5vw,5rem)] font-semibold leading-none tracking-tight">
              <LiveNumber value={it.value} format={it.fmt} className="gradient-num" />
            </dd>
            <dt className="mt-3 max-w-[24ch] text-ink-mute">{it.label}{mode === "replay" ? " (replay)" : ""}</dt>
          </div>
        ))}
      </dl>
    </section>
  );
}

function MeetAegis() {
  const open = useUi((s) => s.openAssistant);
  return (
    <section aria-labelledby="meet-title" className="mx-auto grid max-w-[1440px] items-center gap-12 px-[var(--gutter)] pb-32 lg:grid-cols-2">
      <div className="glow-border relative rounded-[var(--radius)]">
        <MeetStage />
      </div>
      <div>
        <h2 id="meet-title" className="font-display text-4xl font-semibold tracking-tight md:text-6xl"><BlurWords text="Meet AEGIS. Ask it anything." shimmer /></h2>
        <p className="mt-6 max-w-[42ch] text-lg leading-relaxed text-ink-soft">
          The assistant answers from this project's own docs and its live state, and every answer is a real request through the gateway, with its trace attached.
        </p>
        <div className="glass glow-border relative mt-10 max-w-[480px] p-5">
          <p className="text-ink">"What happens when the primary model dies?"</p>
          <Magnetic>
            <button type="button" className="btn btn-primary mt-4" onClick={() => open("What happens when the primary model dies?")}>
              Ask AEGIS
            </button>
          </Magnetic>
        </div>
      </div>
    </section>
  );
}

export default function Home() {
  return (
    <>
      <Hero />
      <div id="enter" />
      <Dive />
      <ProofStrip />
      <MeetAegis />
      <footer className="px-[var(--gutter)] pb-16">
        <p className="text-shimmer font-display text-[clamp(3rem,16vw,15rem)] font-bold leading-none tracking-[-0.05em] opacity-80">AEGIS</p>
        <p className="mt-4 text-sm text-ink-mute">FastAPI · Redis Stack · MLX · Ollama · OpenTelemetry · Jaeger. Runs on one Mac. Costs shown are estimated cloud equivalents; local inference has no bill.</p>
      </footer>
    </>
  );
}
