// AEGIS's living face: an LED dot-matrix drawn over the hover robot's screen, clipped to the
// measured screen polygon. The expression follows real gateway state.
import { motion } from "motion/react";
import { useEffect, useId, useState } from "react";
import { HOVER, polygon } from "../config/robot";
import { useReducedMotion } from "../lib/motion";

export type Mood = "happy" | "concerned" | "alarmed" | "thinking" | "surprised" | "sleep";

const EYE = {
  happy: { sy: 1, sx: 1, dy: 0 },
  surprised: { sy: 1.18, sx: 1.12, dy: -4 },
  concerned: { sy: 0.55, sx: 1.05, dy: 10 },
  alarmed: { sy: 1.25, sx: 1.25, dy: -6 },
  thinking: { sy: 0.8, sx: 0.95, dy: -16 },
  sleep: { sy: 0.12, sx: 1.1, dy: 22 },
};

function mouthPath(mood: Mood, m: { cx: number; cy: number; w: number; h: number }) {
  const l = m.cx - m.w / 2;
  const r = m.cx + m.w / 2;
  const y = m.cy - 6;
  switch (mood) {
    case "concerned":
      return `M ${l + 14} ${y + 26} Q ${m.cx} ${y + 4} ${r - 14} ${y + 26}`;
    case "alarmed":
      return `M ${m.cx - 22} ${y + 14} a 22 20 0 1 0 44 0 a 22 20 0 1 0 -44 0`;
    case "thinking":
      return `M ${l + 26} ${y + 18} L ${r - 26} ${y + 18}`;
    case "surprised":
      return `M ${m.cx - 16} ${y + 16} a 16 16 0 1 0 32 0 a 16 16 0 1 0 -32 0`;
    case "sleep":
      return `M ${l + 30} ${y + 18} Q ${m.cx} ${y + 26} ${r - 30} ${y + 18}`;
    default:
      return `M ${l} ${y} Q ${m.cx} ${y + m.h} ${r} ${y}`;
  }
}

export default function RobotFace({ mood, glow = 1 }: { mood: Mood; glow?: number }) {
  const id = useId().replace(/:/g, "");
  const reduced = useReducedMotion();
  const [blink, setBlink] = useState(false);

  // Blink every few seconds, on a fixed rhythm (decorative; no data involved).
  useEffect(() => {
    if (reduced || mood === "sleep") return;
    let n = 0;
    const gaps = [3400, 5200, 2600, 4400, 6100];
    let t: number;
    const schedule = () => {
      t = window.setTimeout(() => {
        setBlink(true);
        window.setTimeout(() => setBlink(false), 140);
        n++;
        schedule();
      }, gaps[n % gaps.length]);
    };
    schedule();
    return () => window.clearTimeout(t);
  }, [reduced, mood]);

  const e = EYE[mood];
  const { left, right } = HOVER.eyes;
  const eyeTransition = reduced ? { duration: 0 } : { type: "spring" as const, stiffness: 320, damping: 22 };
  const thinkingDots = mood === "thinking" && !reduced;

  return (
    <g aria-hidden="true">
      <defs>
        <clipPath id={`screen-${id}`}>
          <polygon points={polygon(HOVER.screen)} />
        </clipPath>
        <radialGradient id={`glass-${id}`} cx="45%" cy="30%" r="75%">
          <stop offset="0%" stopColor="var(--space-2)" />
          <stop offset="70%" stopColor="var(--screen)" />
          <stop offset="100%" stopColor="var(--space-0)" />
        </radialGradient>
        <pattern id={`grid-${id}`} width="7" height="7" patternUnits="userSpaceOnUse">
          <rect x="1" y="1" width="5" height="5" rx="1" style={{ fill: "var(--cyan)" }} opacity="0.06" />
        </pattern>
        <pattern id={`led-${id}`} width="7" height="7" patternUnits="userSpaceOnUse">
          <rect x="0.8" y="0.8" width="5.4" height="5.4" rx="1.2" style={{ fill: "var(--cyan-hi)" }} />
        </pattern>
        <filter id={`bloom-${id}`} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
        <linearGradient id={`sheen-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--ink)" stopOpacity="0.10" />
          <stop offset="45%" stopColor="var(--ink)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <g clipPath={`url(#screen-${id})`}>
        <polygon points={polygon(HOVER.screen)} fill={`url(#glass-${id})`} />
        <polygon points={polygon(HOVER.screen)} fill={`url(#grid-${id})`} />
        {/* glow layer */}
        <g filter={`url(#bloom-${id})`} opacity={0.75 * glow} style={{ color: "var(--cyan)" }}>
          {[left, right].map((eye, i) => (
            <motion.rect
              key={i}
              x={eye.cx - eye.w / 2}
              y={eye.cy - eye.h / 2}
              width={eye.w}
              height={eye.h}
              rx={eye.w / 2.4}
              style={{ fill: "var(--cyan)", transformOrigin: `${eye.cx}px ${eye.cy}px`, transformBox: "view-box" }}
              animate={{ scaleY: blink ? 0.08 : e.sy, scaleX: e.sx, y: e.dy }}
              transition={eyeTransition}
            />
          ))}
          <motion.path d={mouthPath(mood, HOVER.mouth)} fill="none" style={{ stroke: "var(--cyan)" }} strokeWidth={20} strokeLinecap="round" animate={{ d: mouthPath(mood, HOVER.mouth) }} transition={eyeTransition} />
        </g>
        {/* LED dot layer */}
        {[left, right].map((eye, i) => (
          <motion.rect
            key={i}
            x={eye.cx - eye.w / 2}
            y={eye.cy - eye.h / 2}
            width={eye.w}
            height={eye.h}
            rx={eye.w / 2.4}
            fill={`url(#led-${id})`}
            style={{ transformOrigin: `${eye.cx}px ${eye.cy}px`, transformBox: "view-box" }}
            animate={{ scaleY: blink ? 0.08 : e.sy, scaleX: e.sx, y: e.dy }}
            transition={eyeTransition}
          />
        ))}
        <motion.path
          d={mouthPath(mood, HOVER.mouth)}
          fill="none"
          stroke={`url(#led-${id})`}
          strokeWidth={15}
          strokeLinecap="round"
          animate={{ d: mouthPath(mood, HOVER.mouth) }}
          transition={eyeTransition}
        />
        {thinkingDots &&
          [0, 1, 2].map((i) => (
            <motion.rect
              key={i}
              x={HOVER.mouth.cx - 30 + i * 24}
              y={HOVER.mouth.cy + 30}
              width={10}
              height={10}
              rx={2}
              style={{ fill: "var(--cyan-hi)" }}
              animate={{ opacity: [0.2, 1, 0.2] }}
              transition={{ duration: 1, repeat: Infinity, delay: i * 0.2 }}
            />
          ))}
        <polygon points={polygon(HOVER.screen)} fill={`url(#sheen-${id})`} />
      </g>
    </g>
  );
}
