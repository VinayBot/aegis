// The site's motion kit. Everything here animates only transform, opacity and filter, and each
// effect has a reduced-motion form that keeps the content.
import { motion, useMotionValue, useSpring, type Variants } from "motion/react";
import { Children, useEffect, useRef, type ReactNode } from "react";
import { backendLabel, fmtMs } from "../../lib/format";
import { onEvent } from "../../lib/live";
import type { GatewayEvent } from "../../lib/types";
import { EASE_UI, useReducedMotion } from "../../lib/motion";

/** One global listener feeds every .glass / .spot element the pointer position, so each panel
 *  carries a soft light that follows the cursor (CSS reads --mx/--my). */
export function SpotlightProvider() {
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const el = (e.target as HTMLElement | null)?.closest<HTMLElement>(".glass, .spot");
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${e.clientX - r.left}px`);
      el.style.setProperty("--my", `${e.clientY - r.top}px`);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, []);
  return null;
}

/** A soft light that trails the cursor across the whole page. */
export function CursorGlow() {
  const reduced = useReducedMotion();
  const x = useMotionValue(-400);
  const y = useMotionValue(-400);
  const sx = useSpring(x, { stiffness: 120, damping: 20, mass: 0.6 });
  const sy = useSpring(y, { stiffness: 120, damping: 20, mass: 0.6 });
  useEffect(() => {
    if (reduced || window.matchMedia("(pointer: coarse)").matches) return;
    const onMove = (e: PointerEvent) => {
      x.set(e.clientX - 260);
      y.set(e.clientY - 260);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [reduced, x, y]);
  if (reduced) return null;
  return <motion.div aria-hidden="true" className="cursor-glow" style={{ x: sx, y: sy }} />;
}

const revealVariants: Variants = {
  hidden: { opacity: 0, y: 26, filter: "blur(10px)" },
  shown: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.7, ease: EASE_UI } },
};

/** Staggered entrance for a section's parts (heading, then text, then figure), once. */
export function Reveal({ children, className = "", stagger = 0.08, as = "div", delay = 0 }: { children: ReactNode; className?: string; stagger?: number; as?: "div" | "section" | "ul" | "ol" | "dl"; delay?: number }) {
  const reduced = useReducedMotion();
  const Tag = motion[as] as typeof motion.div;
  if (reduced) {
    const Plain = as;
    return <Plain className={className}>{children}</Plain>;
  }
  return (
    <Tag className={className} initial="hidden" whileInView="shown" viewport={{ once: true, margin: "-8% 0px" }}
      transition={{ staggerChildren: stagger, delayChildren: delay }}>
      {Children.map(children, (child) => (child === null || child === false ? child : <motion.div variants={revealVariants}>{child}</motion.div>))}
    </Tag>
  );
}

/** Headline that rises word by word out of a blur, with a slow shimmer across it afterwards. */
export function BlurWords({ text, className = "", shimmer = false, delay = 0 }: { text: string; className?: string; shimmer?: boolean; delay?: number }) {
  const reduced = useReducedMotion();
  const words = text.split(" ");
  return (
    <span className={`${className} ${shimmer ? "text-shimmer" : ""}`}>
      <span className="sr-only">{text}</span>
      {words.map((w, i) => (
        <motion.span key={i} aria-hidden="true" className="inline-block whitespace-pre"
          initial={reduced ? false : { opacity: 0, y: "0.45em", filter: "blur(12px)" }}
          whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }} viewport={{ once: true }}
          transition={{ duration: 0.8, ease: EASE_UI, delay: delay + i * 0.07 }}>
          {w}{i < words.length - 1 ? " " : ""}
        </motion.span>
      ))}
    </span>
  );
}

/** Pulls its child gently toward the pointer while hovered. */
export function Magnetic({ children, strength = 0.28 }: { children: ReactNode; strength?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();
  const x = useSpring(0, { stiffness: 220, damping: 16 });
  const y = useSpring(0, { stiffness: 220, damping: 16 });
  return (
    <motion.span ref={ref} className="inline-block" style={{ x, y }}
      onPointerMove={(e) => {
        if (reduced || !ref.current) return;
        const r = ref.current.getBoundingClientRect();
        x.set((e.clientX - (r.left + r.width / 2)) * strength);
        y.set((e.clientY - (r.top + r.height / 2)) * strength);
      }}
      onPointerLeave={() => { x.set(0); y.set(0); }}>
      {children}
    </motion.span>
  );
}

/** A live ribbon of the gateway's real request outcomes, scrolling right to left. */
export function EventTicker() {
  const track = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    const el = track.current;
    if (!el) return;
    return onEvent((e: GatewayEvent) => {
      if (e.type !== "request.end") return;
      const item = document.createElement("span");
      const kind = e.status !== "ok" ? "bad" : e.cache === "exact" || e.cache === "semantic" || e.cache === "coalesced" ? "cached" : e.hedged || Number(e.failovers) > 0 ? "hedge" : "ok";
      item.className = `ticker-item ticker-${kind}`;
      const what = e.status !== "ok" ? `${String(e.http_status)} ${String(e.status)}` : e.cache === "miss" || e.cache === "bypass" ? `served by ${backendLabel(e.backend as string)}${e.hedged ? " (hedged)" : ""}` : `${String(e.cache)} ${e.cache === "coalesced" ? "" : "hit"}`;
      item.textContent = `${String(e.tenant)} · ${what} · ${fmtMs(Number(e.latency_ms))}`;
      el.prepend(item);
      while (el.children.length > 24) el.lastElementChild?.remove();
    });
  }, []);
  return (
    <div className="ticker" aria-label="Live request outcomes" role="log" aria-live="off">
      <span className="ticker-label"><span className="status-dot s-live animate-pulse" aria-hidden="true" />live</span>
      <div ref={track} className={`ticker-track ${reduced ? "" : "ticker-anim"}`}>
        <span className="ticker-item ticker-idle">Waiting for traffic: send a request from the Playground</span>
      </div>
    </div>
  );
}

/** A short warp flash between pages, like jumping between decks. */
export function Warp({ trigger }: { trigger: string }) {
  const reduced = useReducedMotion();
  if (reduced) return null;
  return (
    <motion.div key={trigger} aria-hidden="true" className="warp" initial={{ opacity: 0.9, scale: 0.6 }} animate={{ opacity: 0, scale: 1.6 }} transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }} />
  );
}
