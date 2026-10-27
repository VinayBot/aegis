import { AnimatePresence, motion } from "motion/react";
import { Suspense, lazy } from "react";
import { useSystemMood } from "./HoverRobot";
import { CursorGlow, EventTicker, SpotlightProvider, Warp } from "./fx";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useLive } from "../lib/live";
import { EASE_UI, useMotionPause, useReducedMotion } from "../lib/motion";

const Assistant = lazy(() => import("./Assistant"));
const Aurora = lazy(() => import("./fx/Aurora"));

const LINKS = [
  ["/", "Home"],
  ["/architecture", "Architecture"],
  ["/control-room", "Control Room"],
  ["/chaos", "Chaos Lab"],
  ["/playground", "Playground"],
  ["/traces", "Traces"],
  ["/docs", "Docs"],
] as const;

export function LiveChip() {
  const mode = useLive((s) => s.mode);
  const ws = useLive((s) => s.wsConnected);
  const label = mode === "replay" ? "Replay" : mode === "connecting" ? "Connecting" : ws ? "Live" : "Disconnected";
  const tone = mode === "replay" ? "s-degraded" : mode === "live" && ws ? "s-live" : mode === "connecting" ? "s-unknown" : "s-down";
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-soft" role="status" aria-live="polite">
      <span className={`status-dot ${tone} ${mode === "live" && ws ? "animate-pulse" : ""}`} aria-hidden="true" />
      {label}
    </span>
  );
}

function ReplayBanner() {
  const mode = useLive((s) => s.mode);
  const at = useLive((s) => s.fixtureRecordedAt);
  if (mode !== "replay") return null;
  return (
    <div role="alert" className="relative z-40 flex flex-wrap items-center justify-center gap-x-3 px-4 py-2 text-center text-sm font-semibold" style={{ background: "var(--amber)", color: "var(--space-0)" }}>
      <span>REPLAY: backend offline</span>
      <span className="font-normal">
        Playing back recorded real traffic{at ? ` from ${new Date(at * 1000).toLocaleString()}` : ""}. Start the gateway with <code>make dev</code> to go live.
      </span>
    </div>
  );
}

function MotionToggle() {
  const paused = useMotionPause((s) => s.paused);
  const toggle = useMotionPause((s) => s.toggle);
  return (
    <button type="button" onClick={toggle} aria-pressed={paused} className="hidden min-h-9 rounded-lg px-3 text-sm text-ink-mute hover:text-ink sm:block">
      {paused ? "Resume motion" : "Pause motion"}
    </button>
  );
}

/** The whole screen edge pulses red while any breaker is open: real state, not decoration. */
function Alarm() {
  const mood = useSystemMood();
  return mood === "alarmed" ? <div className="alarm-vignette" aria-hidden="true" /> : null;
}

export default function Shell() {
  const location = useLocation();
  const reduced = useReducedMotion();
  return (
    <div className="grain min-h-screen">
      <Suspense fallback={null}><Aurora /></Suspense>
      <SpotlightProvider />
      <CursorGlow />
      <Alarm />
      <a href="#main" className="skip-link">Skip to content</a>
      <ReplayBanner />
      <header className="sticky top-0 z-30" style={{ background: "var(--scrim)", backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)" }}>
        <nav aria-label="Main" className="mx-auto flex h-16 max-w-[1440px] items-center gap-6 px-[var(--gutter)]">
          <NavLink to="/" className="flex items-center gap-2.5 font-display text-xl font-bold tracking-tight text-ink" aria-label="AEGIS home">
            <img src="/assets/robot-orb-96.webp" alt="" width={28} height={28} className="h-7 w-7 rounded-full" />
            AEGIS
          </NavLink>
          <ul className="ml-2 hidden items-center gap-1 lg:flex">
            {LINKS.slice(1).map(([to, label]) => (
              <li key={to}>
                <NavLink to={to} className={({ isActive }) => `relative block rounded-lg px-3 py-2 text-[0.92rem] transition-colors ${isActive ? "text-ink" : "text-ink-mute hover:text-ink"}`}>
                  {({ isActive }) => (
                    <>
                      {isActive && <motion.span layoutId="nav-pill" className="nav-pill" transition={{ type: "spring", stiffness: 380, damping: 32 }} />}
                      {label}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="ml-auto flex items-center gap-4">
            <MotionToggle />
            <LiveChip />
            <details className="relative lg:hidden">
              <summary className="btn btn-quiet list-none" aria-label="Open menu">Menu</summary>
              <ul className="glass glass-strong absolute right-0 mt-2 w-56 p-2">
                {LINKS.map(([to, label]) => (
                  <li key={to}>
                    <NavLink to={to} className="block rounded-lg px-3 py-2.5 text-ink-soft hover:text-ink">{label}</NavLink>
                  </li>
                ))}
              </ul>
            </details>
          </div>
        </nav>
        <EventTicker />
      </header>
      <Warp trigger={location.pathname} />
      <AnimatePresence mode="wait">
        <motion.main
          id="main"
          key={location.pathname}
          initial={reduced ? false : { opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduced ? undefined : { opacity: 0, y: -8 }}
          transition={{ duration: 0.32, ease: EASE_UI }}
          className="origin-top pb-32 sm:pb-0" // on phones, room to scroll the last controls clear of the docked robot
        >
          <Suspense fallback={<div className="min-h-[60vh]" aria-busy="true" />}>
            <Outlet />
          </Suspense>
        </motion.main>
      </AnimatePresence>
      <Suspense fallback={null}>
        <Assistant />
      </Suspense>
    </div>
  );
}
