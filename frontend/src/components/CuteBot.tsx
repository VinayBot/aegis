// AEGIS's flying form, drawn in code (an original design): a round head with big glowing ring
// eyes and a smile, an ear speaker and antenna, a small body, and two thruster pods.
//   sleep:  eyes show two LED dashes (resting)
//   awake:  round pupils that glance around
//   boot:   a bright flash in both eyes while it wakes up
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { useReducedMotion } from "../lib/motion";

export type BotMood = "sleep" | "awake" | "boot";

function Eye({ cx, cy, mood, look, blink }: { cx: number; cy: number; mood: BotMood; look: [number, number]; blink: boolean }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={29} style={{ fill: "var(--bot-dark)" }} />
      <circle cx={cx} cy={cy} r={29} fill="none" strokeWidth={9} style={{ stroke: "var(--cyan)" }} filter="url(#bot-glow)" opacity={0.75} />
      <circle cx={cx} cy={cy} r={29} fill="none" strokeWidth={4.5} style={{ stroke: "var(--cyan-hi)" }} />
      <motion.g style={{ transformOrigin: `${cx}px ${cy}px`, transformBox: "view-box" }} animate={{ scaleY: blink ? 0.08 : 1 }} transition={{ duration: 0.08 }}>
        {mood === "sleep" ? (
          <>
            <rect x={cx - 14} y={cy - 6} width={28} height={4.5} rx={2} style={{ fill: "var(--cyan-hi)" }} />
            <rect x={cx - 14} y={cy + 3} width={18} height={4.5} rx={2} style={{ fill: "var(--cyan)" }} />
          </>
        ) : (
          <motion.g animate={{ x: look[0], y: look[1] }} transition={{ type: "spring", stiffness: 140, damping: 14 }}>
            <circle cx={cx} cy={cy} r={mood === "boot" ? 15 : 11} style={{ fill: "var(--cyan-hi)" }} filter="url(#bot-glow)" />
            <circle cx={cx} cy={cy} r={mood === "boot" ? 12 : 9} style={{ fill: "var(--cyan-hi)" }} />
            <circle cx={cx + 4} cy={cy - 4} r={3} style={{ fill: "var(--ink)" }} />
          </motion.g>
        )}
      </motion.g>
      {mood === "boot" && <motion.circle cx={cx} cy={cy} r={29} style={{ fill: "var(--cyan-hi)" }} initial={{ opacity: 0.9 }} animate={{ opacity: 0 }} transition={{ duration: 0.6 }} />}
    </g>
  );
}

export default function CuteBot({ mood, className = "" }: { mood: BotMood; className?: string }) {
  const reduced = useReducedMotion();
  const [blink, setBlink] = useState(false);
  const [look, setLook] = useState<[number, number]>([0, 0]);

  // blinks and little glances on a fixed rhythm (decorative)
  useEffect(() => {
    if (reduced || mood === "sleep") return;
    const glances: [number, number][] = [[0, 0], [-6, -2], [5, 1], [0, 3], [6, -3], [0, 0]];
    let n = 0;
    const t = window.setInterval(() => {
      n++;
      if (n % 3 === 0) {
        setBlink(true);
        window.setTimeout(() => setBlink(false), 120);
      } else setLook(glances[n % glances.length]);
    }, 1300);
    return () => window.clearInterval(t);
  }, [reduced, mood]);

  return (
    <svg viewBox="0 0 220 214" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="bot-shell" x1="0.2" y1="0" x2="0.8" y2="1">
          <stop offset="0" style={{ stopColor: "var(--bot-shell-hi)" }} />
          <stop offset="1" style={{ stopColor: "var(--bot-shell-lo)" }} />
        </linearGradient>
        <filter id="bot-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4" />
        </filter>
      </defs>

      {/* thruster pods, behind the body */}
      {[56, 164].map((x) => (
        <g key={x}>
          <circle cx={x} cy={176} r={24} fill="url(#bot-shell)" />
          <circle cx={x} cy={180} r={14} style={{ fill: "var(--bot-dark)" }} />
          <circle cx={x} cy={180} r={14} fill="none" strokeWidth={2.5} style={{ stroke: "var(--cyan)" }} opacity={0.8} />
        </g>
      ))}

      {/* body */}
      <rect x={82} y={130} width={56} height={12} rx={6} style={{ fill: "var(--bot-dark)" }} />
      <rect x={68} y={136} width={84} height={50} rx={20} fill="url(#bot-shell)" />
      <rect x={92} y={152} width={36} height={16} rx={4} style={{ fill: "var(--bot-panel)" }} />
      {[100, 110, 120].map((x, i) => (
        <motion.circle key={x} cx={x} cy={160} r={2.6} style={{ fill: "var(--cyan)" }}
          animate={reduced ? undefined : { opacity: [0.3, 1, 0.3] }} transition={{ duration: 1.4, repeat: Infinity, delay: i * 0.25 }} />
      ))}

      {/* antenna */}
      <line x1={180} y1={36} x2={194} y2={6} strokeWidth={4} strokeLinecap="round" style={{ stroke: "var(--bot-shell-lo)" }} />
      <motion.circle cx={194} cy={6} r={5} style={{ fill: "var(--cyan-hi)" }} filter="url(#bot-glow)"
        animate={reduced ? undefined : { opacity: [1, 0.25, 1] }} transition={{ duration: 1.6, repeat: Infinity }} />
      <circle cx={194} cy={6} r={3} style={{ fill: "var(--cyan-hi)" }} />

      {/* head */}
      <rect x={14} y={14} width={186} height={124} rx={60} fill="url(#bot-shell)" />
      <ellipse cx={74} cy={34} rx={42} ry={12} style={{ fill: "var(--bot-shell-hi)" }} opacity={0.9} />

      {/* ear speaker */}
      <circle cx={198} cy={80} r={22} fill="url(#bot-shell)" />
      <circle cx={198} cy={80} r={15} fill="none" strokeWidth={4} style={{ stroke: "var(--cyan)" }} filter="url(#bot-glow)" />
      <circle cx={198} cy={80} r={15} fill="none" strokeWidth={2.5} style={{ stroke: "var(--cyan-hi)" }} />
      <circle cx={198} cy={80} r={8} style={{ fill: "var(--bot-shell-lo)" }} />

      <Eye cx={74} cy={72} mood={mood} look={look} blink={blink} />
      <Eye cx={142} cy={72} mood={mood} look={look} blink={blink} />

      {/* smile */}
      <path d={mood === "sleep" ? "M 94 112 Q 108 118 122 112" : "M 88 106 Q 108 128 128 106 Z"} strokeWidth={mood === "sleep" ? 5 : 0} strokeLinecap="round"
        style={{ fill: mood === "sleep" ? "none" : "var(--bot-dark)", stroke: "var(--bot-dark)" }} />
    </svg>
  );
}
