// "Enter the brain": a pinned, scroll-driven dive. The robot's face screen grows to fill the
// viewport, dissolves into a neural interior, and the camera flies through five regions, each a
// real subsystem with one live number. Native scrolling is never hijacked: this is a tall section
// with a sticky stage, driven by Motion's useScroll. Reduced motion gets the same content as a
// vertical run of static cards.
import { motion, useScroll, useTransform, type MotionValue } from "motion/react";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { HOVER } from "../config/robot";
import { fmtInt, fmtMs, fmtPct, fmtUsd } from "../lib/format";
import { useLive } from "../lib/live";
import { useNearViewport, useOnScreen, useReducedMotion } from "../lib/motion";
import HoverRobot from "./HoverRobot";

const Brain = lazy(() => import("../scenes/Brain"));

interface Region {
  id: string;
  name: string;
  what: string;
  metric: string;
  value: (d: Live) => string;
  tone: string;
}

interface Live {
  requests: number | undefined;
  guardP95: number | null | undefined;
  hitRate: number | null | undefined;
  coalesced: number | undefined;
  hedged: number | undefined;
  failedOver: number | undefined;
  trips: number | undefined;
  saved: number | undefined;
  tokens: number | undefined;
}

export const REGIONS: Region[] = [
  { id: "gate", name: "The Gate", tone: "var(--cyan)", what: "Every prompt is authenticated, scrubbed of personal data and checked for injection before anything else can see it.", metric: "guardrail p95", value: (d) => fmtMs(d.guardP95) },
  { id: "memory", name: "Memory", tone: "var(--violet)", what: "An exact or a paraphrased question that was answered before is replayed from Redis in milliseconds, at zero model cost.", metric: "cache hit rate, all time", value: (d) => fmtPct(d.hitRate) },
  { id: "hive", name: "The Hive", tone: "var(--green)", what: "Identical questions asked at the same moment share a single model call; everyone gets the same stream.", metric: "requests coalesced", value: (d) => fmtInt(d.coalesced) },
  { id: "reflexes", name: "The Reflexes", tone: "var(--amber)", what: "A slow model gets raced, a failing one is cut off by its breaker, and traffic reroutes before the user notices.", metric: "hedged + failed over + breaker trips", value: (d) => `${fmtInt(d.hedged)} · ${fmtInt(d.failedOver)} · ${fmtInt(d.trips)}` },
  { id: "ledger", name: "The Ledger", tone: "var(--cyan-hi)", what: "Every request is counted: tokens, latency, and what it would have cost on a cloud model.", metric: "saved by cache + coalescing (est. cloud equivalent)", value: (d) => fmtUsd(d.saved) },
];

function useLiveNumbers(): Live {
  const totals = useLive((s) => s.metrics?.totals);
  const arch = useLive((s) => s.architecture);
  const guard = arch?.nodes.find((n) => n.id === "guardrails")?.stats.p95_ms;
  const served = totals ? totals.served : undefined;
  return {
    requests: totals?.requests,
    guardP95: guard,
    hitRate: totals && served ? (totals.cache_exact + totals.cache_semantic) / served : null,
    coalesced: totals?.coalesced,
    hedged: totals?.hedged,
    failedOver: totals?.failed_over,
    trips: totals?.breaker_trips,
    saved: totals?.saved_est_usd,
    tokens: totals ? totals.tokens_in + totals.tokens_out : undefined,
  };
}

function Caption({ region, index, progress, live }: { region: Region; index: number; progress: MotionValue<number>; live: Live }) {
  const start = 0.2 + index * 0.13;
  const opacity = useTransform(progress, [start, start + 0.03, start + 0.095, start + 0.12], [0, 1, 1, 0]);
  const y = useTransform(progress, [start, start + 0.03], [24, 0]);
  return (
    <motion.article style={{ opacity, y }} className="glass glow-border pointer-events-none absolute bottom-[10vh] left-[var(--gutter)] w-[min(460px,calc(100vw-2*var(--gutter)))] p-6">
      <h3 className="font-display text-3xl font-semibold tracking-tight" style={{ color: region.tone }}>{region.name}</h3>
      <p className="mt-2 text-[1.02rem] leading-relaxed text-ink-soft">{region.what}</p>
      <p className="mt-4 flex items-baseline gap-3">
        <span className="font-display text-4xl font-semibold text-ink font-tabular">{region.value(live)}</span>
        <span className="text-sm text-ink-mute">{region.metric}</span>
      </p>
    </motion.article>
  );
}

function StaticDive({ live }: { live: Live }) {
  return (
    <section aria-labelledby="dive-title" className="mx-auto max-w-[1100px] px-[var(--gutter)] py-24">
      <h2 id="dive-title" className="font-display text-4xl font-semibold tracking-tight md:text-5xl">Inside AEGIS, region by region</h2>
      <p className="mt-4 max-w-[60ch] text-lg text-ink-soft">Five subsystems every request passes through, each with a number from the running gateway.</p>
      <ol className="mt-12 grid gap-5">
        {REGIONS.map((r) => (
          <li key={r.id} className="glass grid gap-4 p-6 md:grid-cols-[1fr_auto] md:items-end">
            <div>
              <h3 className="font-display text-2xl font-semibold" style={{ color: r.tone }}>{r.name}</h3>
              <p className="mt-2 text-ink-soft">{r.what}</p>
            </div>
            <p className="text-right">
              <span className="block font-display text-3xl font-semibold font-tabular">{r.value(live)}</span>
              <span className="text-sm text-ink-mute">{r.metric}</span>
            </p>
          </li>
        ))}
      </ol>
      <Link to="/architecture" className="btn btn-primary mt-10">See it running live</Link>
    </section>
  );
}

export default function Dive() {
  const reduced = useReducedMotion();
  const live = useLiveNumbers();
  const ref = useRef<HTMLElement>(null);
  const near = useNearViewport(ref, "400px");
  // The 3D scene (three.js, the largest chunk) loads on the first scroll, never during the first
  // screen; the robot zoom that opens the dive covers the load.
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    const arm = () => setArmed(true);
    window.addEventListener("scroll", arm, { once: true, passive: true });
    if (window.scrollY > 0) arm();
    return () => window.removeEventListener("scroll", arm);
  }, []);
  const onScreen = useOnScreen(ref);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });

  // Phase A: zoom into the face screen (its centre is the transform origin).
  const screenCx = (HOVER.screen.reduce((a, p) => a + p[0], 0) / HOVER.screen.length / HOVER.width) * 100;
  const screenCy = (HOVER.screen.reduce((a, p) => a + p[1], 0) / HOVER.screen.length / HOVER.height) * 100;
  const robotScale = useTransform(scrollYProgress, [0, 0.14], [1, 9]);
  const robotOpacity = useTransform(scrollYProgress, [0.1, 0.17], [1, 0]);
  const brainOpacity = useTransform(scrollYProgress, [0.11, 0.19], [0, 1]);
  const introOpacity = useTransform(scrollYProgress, [0, 0.05], [1, 0]);
  const exitOpacity = useTransform(scrollYProgress, [0.88, 0.94], [0, 1]);
  const exitPointer = useTransform(scrollYProgress, (v) => (v > 0.88 ? "auto" : "none"));

  if (reduced) return <StaticDive live={live} />;

  return (
    <section ref={ref} aria-labelledby="dive-title" className="relative" style={{ height: "620vh" }}>
      <div className="sticky top-0 h-screen overflow-hidden">
        <h2 id="dive-title" className="sr-only">Inside AEGIS: a dive through the gateway's five subsystems</h2>
        <motion.div style={{ opacity: brainOpacity }} className="absolute inset-0">
          {near && armed && (
            <Suspense fallback={null}>
              <Brain progress={scrollYProgress} active={onScreen} />
            </Suspense>
          )}
        </motion.div>
        <motion.div
          style={{ scale: robotScale, opacity: robotOpacity, transformOrigin: `${screenCx}% ${screenCy}%` }}
          className="absolute left-1/2 top-1/2 w-[min(640px,80vw)] -translate-x-1/2 -translate-y-1/2"
        >
          <HoverRobot interactive={false} mood="thinking" sizes="640px" />
        </motion.div>
        <motion.p style={{ opacity: introOpacity }} className="absolute bottom-[8vh] left-0 right-0 text-center text-lg text-ink-soft">
          Keep scrolling: we are going in through the screen.
        </motion.p>
        {REGIONS.map((r, i) => (
          <Caption key={r.id} region={r} index={i} progress={scrollYProgress} live={live} />
        ))}
        <motion.div style={{ opacity: exitOpacity, pointerEvents: exitPointer }} className="absolute inset-x-0 bottom-[12vh] flex flex-col items-center gap-5 px-[var(--gutter)] text-center">
          <p className="text-shimmer font-display text-3xl font-semibold tracking-tight md:text-6xl">Every region is a node in a live graph.</p>
          <Link to="/architecture" className="btn btn-primary">See it running live</Link>
        </motion.div>
      </div>
    </section>
  );
}
