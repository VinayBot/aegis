// The "Meet PRAHARI" stage, in deep space.
//   back:   a soft nebula glow and twinkling stars (the page's galaxy shows through)
//   floor:  a holographic ring that matches the robot's own blue under-glow, with rising motes
//   robot:  the cut-out robot floats, tilts toward the pointer, and its eyes follow the pointer
//           (its own pupils are covered and redrawn so they can move, and blink)
//   scan:   a hologram scan line sweeps over the robot's silhouette every few seconds
//   bubble: a typewriter cycling questions; clicking one asks PRAHARI for real
import { motion, useMotionValue, useSpring, useTransform } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import regions from "../config/robotRegions.json";
import { mulberry32 } from "../lib/rng";
import { useOnScreen, useReducedMotion } from "../lib/motion";
import { useUi } from "../lib/ui";

const MEET = regions.meet as { width: number; height: number; eyes: Record<"left" | "right", { cx: number; cy: number; r: number; px: number; py: number }> };
const SRC = "/assets/robot-meet";

const QUESTIONS = [
  "What happens when the primary model dies?",
  "How does the semantic cache decide a hit?",
  "Why is busy not the same as broken?",
  "Is the primary healthy right now?",
];

function useTypewriter(lines: string[], active: boolean) {
  const [i, setI] = useState(0);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    const line = lines[i];
    const t = window.setTimeout(() => {
      if (n < line.length) setN(n + 1);
      else {
        setN(0);
        setI((i + 1) % lines.length);
      }
    }, n < line.length ? 38 : 2600);
    return () => window.clearTimeout(t);
  }, [i, n, lines, active]);
  return { text: active ? lines[i].slice(0, n) : lines[0], full: lines[i] };
}

export default function MeetStage() {
  const ref = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const onScreen = useOnScreen(ref);
  const open = useUi((s) => s.openAssistant);
  const live = onScreen && !reduced;
  const { text, full } = useTypewriter(QUESTIONS, live);
  const [blink, setBlink] = useState(false);

  const px = useMotionValue(0);
  const py = useMotionValue(0);
  const sx = useSpring(px, { stiffness: 60, damping: 18 });
  const sy = useSpring(py, { stiffness: 60, damping: 18 });
  const glowX = useTransform(sx, [-1, 1], [16, -16]);
  const midX = useTransform(sx, [-1, 1], [-8, 8]);
  const botX = useTransform(sx, [-1, 1], [-18, 18]);
  const botRot = useTransform(sx, [-1, 1], [-8, 8]);
  const botTilt = useTransform(sy, [-1, 1], [6, -6]);
  const eyeX = useTransform(sx, [-1, 1], [-17, 17]);
  const eyeY = useTransform(sy, [-1, 1], [-13, 13]);

  useEffect(() => {
    if (reduced) return;
    const onMove = (e: PointerEvent) => {
      const r = ref.current?.getBoundingClientRect();
      if (!r) return;
      px.set(Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (r.width / 1.2))));
      py.set(Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height * 0.35)) / (r.height / 1.2))));
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [px, py, reduced]);

  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => {
      setBlink(true);
      window.setTimeout(() => setBlink(false), 140);
    }, 4200);
    return () => window.clearInterval(t);
  }, [live]);

  const stars = useMemo(() => {
    const rnd = mulberry32(21);
    return Array.from({ length: 80 }, () => ({ x: rnd() * 100, y: rnd() * 85, s: 0.8 + rnd() * 2, d: 1.5 + rnd() * 3.5, delay: rnd() * 4 }));
  }, []);
  const motes = useMemo(() => {
    const rnd = mulberry32(5);
    return Array.from({ length: 24 }, () => ({ x: 28 + rnd() * 44, d: 3 + rnd() * 4, delay: rnd() * 5, s: 2 + rnd() * 3 }));
  }, []);

  const eyes = [MEET.eyes.left, MEET.eyes.right];

  return (
    <div ref={ref} className="meet-stage relative aspect-[4/5] w-full overflow-hidden rounded-[var(--radius)] sm:aspect-square">
      <motion.div aria-hidden="true" className="meet-nebula absolute inset-[-10%]" style={{ x: glowX }} />
      <motion.div aria-hidden="true" className="absolute inset-0" style={{ x: midX }}>
        {stars.map((s, i) => (
          <span key={i} className={`meet-star ${live ? "meet-twinkle" : ""}`} style={{ left: `${s.x}%`, top: `${s.y}%`, width: s.s, height: s.s, animationDuration: `${s.d}s`, animationDelay: `${s.delay}s` }} />
        ))}
        <svg viewBox="0 0 400 120" className="absolute bottom-[2%] left-1/2 w-[92%] -translate-x-1/2">
          <defs>
            <linearGradient id="ring-g" x1="0" x2="1">
              <stop offset="0" stopColor="var(--cyan)" stopOpacity="0.05" />
              <stop offset="0.5" stopColor="var(--cyan-hi)" />
              <stop offset="1" stopColor="var(--cyan)" stopOpacity="0.05" />
            </linearGradient>
            <radialGradient id="ring-fill">
              <stop offset="0" stopColor="var(--cyan)" stopOpacity="0.55" />
              <stop offset="0.6" stopColor="var(--cyan-deep)" stopOpacity="0.18" />
              <stop offset="1" stopColor="var(--cyan)" stopOpacity="0" />
            </radialGradient>
          </defs>
          <ellipse cx="200" cy="60" rx="160" ry="36" fill="url(#ring-fill)" className={live ? "meet-pulse" : ""} />
          <ellipse cx="200" cy="60" rx="185" ry="42" fill="none" stroke="url(#ring-g)" strokeWidth="1.6" strokeDasharray="3 9" className={live ? "orbit" : ""} />
          <ellipse cx="200" cy="60" rx="130" ry="28" fill="none" stroke="url(#ring-g)" strokeWidth="1.4" className={live ? "orbit-rev" : ""} />
          <ellipse cx="200" cy="60" rx="78" ry="16" fill="none" stroke="var(--cyan-hi)" strokeOpacity="0.55" strokeWidth="1" />
        </svg>
        {live && motes.map((m, i) => (
          <span key={i} className="meet-mote" style={{ left: `${m.x}%`, width: m.s, height: m.s, animationDuration: `${m.d}s`, animationDelay: `${m.delay}s` }} />
        ))}
      </motion.div>

      <motion.div className="absolute bottom-[7%] left-1/2 w-[46%]" style={{ x: botX, translateX: "-50%", rotateY: botRot, rotateX: botTilt, transformPerspective: 900 }}>
        <motion.div animate={live ? { y: [0, -14, 0] } : { y: 0 }} transition={{ duration: 4.2, repeat: Infinity, ease: "easeInOut" }} className="relative">
          <picture>
            <source type="image/avif" srcSet={`${SRC}-380.avif 380w, ${SRC}-514.avif 514w`} sizes="(max-width: 900px) 45vw, 22vw" />
            <img src={`${SRC}-380.webp`} srcSet={`${SRC}-380.webp 380w, ${SRC}-514.webp 514w`} sizes="(max-width: 900px) 45vw, 22vw"
              width={MEET.width} height={MEET.height} loading="lazy" decoding="async" className="relative block h-auto w-full"
              alt="PRAHARI, a white robot with a large round helmet, a dark visor and two glowing cyan eye rings" />
          </picture>
          {/* hologram scan over the robot's silhouette only */}
          {live && <span aria-hidden="true" className="meet-scan" style={{ WebkitMaskImage: `url(${SRC}-380.webp)`, maskImage: `url(${SRC}-380.webp)` }} />}
          <svg viewBox={`0 0 ${MEET.width} ${MEET.height}`} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
            <defs>
              <filter id="meet-eye" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="7" /></filter>
              <radialGradient id="meet-iris">
                <stop offset="0" style={{ stopColor: "var(--space-2)" }} />
                <stop offset="1" style={{ stopColor: "var(--space-0)" }} />
              </radialGradient>
            </defs>
            {eyes.map((e, i) => (
              <g key={i}>
                {/* cover the painted pupil so ours can move */}
                <circle cx={e.cx} cy={e.cy} r={e.r - 6} fill="url(#meet-iris)" />
                <circle cx={e.cx} cy={e.cy} r={e.r} fill="none" strokeWidth="14" style={{ stroke: "var(--cyan)" }} filter="url(#meet-eye)" className={live ? "meet-eye-glow" : ""} />
                <motion.g style={{ x: eyeX, y: eyeY, transformOrigin: `${e.cx}px ${e.cy}px`, transformBox: "view-box" }} animate={{ scaleY: blink ? 0.1 : 1 }} transition={{ duration: 0.08 }}>
                  <circle cx={e.cx} cy={e.cy} r={11} style={{ fill: "var(--cyan-hi)" }} filter="url(#meet-eye)" />
                  <circle cx={e.cx} cy={e.cy} r={7} style={{ fill: "var(--star)" }} />
                  <circle cx={e.cx + 3} cy={e.cy - 3} r={2.4} style={{ fill: "var(--space-0)" }} opacity={0.6} />
                </motion.g>
              </g>
            ))}
          </svg>
        </motion.div>
      </motion.div>

      <button type="button" onClick={() => open(full)} className="meet-bubble glass glass-strong glow-border is-live absolute left-[5%] top-[6%] max-w-[50%] px-4 py-3 text-left">
        <span className="block text-[0.72rem] font-semibold text-cyan">Ask PRAHARI</span>
        <span className="mt-1 block min-h-[2.6em] text-[0.98rem] leading-snug text-ink">
          {text}
          {live && <span className="meet-caret" aria-hidden="true" />}
        </span>
      </button>
    </div>
  );
}
