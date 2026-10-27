// The hovering AEGIS robot: cut-out picture, living LED face, a live chart on its glass tablet,
// an idle bob with a floor shadow that breathes with it, and a spring-smoothed tilt toward the
// pointer. With reduced motion it stands still and the face holds its expression.
import { animate, motion, useMotionValue, useSpring, useTransform } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ASSETS, HOVER, quadMatrix, srcSet } from "../config/robot";
import { useLive } from "../lib/live";
import { useReducedMotion } from "../lib/motion";
import RobotFace, { type Mood } from "./RobotFace";

export function useSystemMood(): Mood {
  const health = useLive((s) => s.health);
  const mode = useLive((s) => s.mode);
  if (mode === "connecting" || !health) return "sleep";
  const comps = Object.entries(health.components);
  if (comps.some(([n, c]) => n.startsWith("backend:") && c.breaker === "open")) return "alarmed";
  if (health.status !== "up" || comps.some(([, c]) => c.status === "down")) return "concerned";
  return "happy";
}

function TabletChart() {
  const series = useLive((s) => s.metrics?.series.rps);
  const mode = useLive((s) => s.mode);
  const values = (series ?? []).slice(-24).map((v) => v ?? 0);
  const max = Math.max(0.2, ...values);
  const last = values.at(-1);
  return (
    <g transform={quadMatrix(HOVER.tablet)} aria-hidden="true">
      <rect x="0" y="0" width="100" height="100" rx="3" style={{ fill: "var(--space-2)" }} opacity="0.68" />
      <text x="5" y="11" fontSize="7.5" style={{ fill: "var(--cyan-hi)", fontFamily: "var(--font-ui)", fontWeight: 600 }}>
        {mode === "replay" ? "RPS · replay" : "RPS · live"}
      </text>
      <text x="95" y="11" fontSize="7.5" textAnchor="end" style={{ fill: "var(--ink)", fontFamily: "var(--font-ui)" }}>
        {last === undefined ? "—" : last.toFixed(2)}
      </text>
      {values.length === 0 && (
        <text x="50" y="58" fontSize="7" textAnchor="middle" style={{ fill: "var(--ink-mute)" }}>waiting for data</text>
      )}
      {values.map((v, i) => {
        const h = (v / max) * 72;
        const w = 100 / 24;
        return (
          <rect key={i} x={2 + i * w * 0.96} y={92 - h} width={w * 0.62} height={Math.max(h, 0.6)} rx="0.6"
            style={{ fill: i === values.length - 1 ? "var(--cyan-hi)" : "var(--cyan)" }}
            opacity={0.35 + 0.65 * (i / Math.max(1, values.length - 1))} />
        );
      })}
    </g>
  );
}

export default function HoverRobot({
  mood: moodOverride,
  interactive = true,
  priority = false,
  className = "",
  sizes = "(max-width: 900px) 90vw, 46vw",
}: {
  mood?: Mood;
  interactive?: boolean;
  priority?: boolean;
  className?: string;
  sizes?: string;
}) {
  const reduced = useReducedMotion();
  const systemMood = useSystemMood();
  const [hovered, setHovered] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const mood: Mood = moodOverride ?? (hovered && systemMood === "happy" ? "surprised" : systemMood);

  // idle bob: one motion value drives both the robot and its shadow
  const bob = useMotionValue(0);
  useEffect(() => {
    if (reduced) return;
    const controls = animate(bob, [0, 1, 0], { duration: 4, repeat: Infinity, ease: "easeInOut" });
    return () => controls.stop();
  }, [bob, reduced]);
  const y = useTransform(bob, [0, 1], [0, -18]);
  const shadowScale = useTransform(bob, [0, 1], [1, 0.82]);
  const shadowOpacity = useTransform(bob, [0, 1], [0.55, 0.32]);

  // pointer parallax, max ±8°
  const px = useMotionValue(0);
  const py = useMotionValue(0);
  const rotY = useSpring(useTransform(px, [-1, 1], [-8, 8]), { stiffness: 90, damping: 16 });
  const rotX = useSpring(useTransform(py, [-1, 1], [8, -8]), { stiffness: 90, damping: 16 });
  useEffect(() => {
    if (!interactive || reduced) return;
    const onMove = (e: PointerEvent) => {
      const r = ref.current?.getBoundingClientRect();
      if (!r) return;
      px.set(Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (window.innerWidth / 2))));
      py.set(Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / (window.innerHeight / 2))));
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [interactive, reduced, px, py]);

  const aspect = useMemo(() => `${HOVER.width} / ${HOVER.height}`, []);

  return (
    <div ref={ref} className={`relative select-none ${className}`} style={{ perspective: 1100 }}>
      <motion.div style={{ y, rotateX: rotX, rotateY: rotY, aspectRatio: aspect }} className="relative w-full will-change-transform">
        <picture>
          <source type="image/avif" srcSet={srcSet(ASSETS.hover.base, ASSETS.hover.widths, "avif")} sizes={sizes} />
          <img
            src={`${ASSETS.hover.base}-760.webp`}
            srcSet={srcSet(ASSETS.hover.base, ASSETS.hover.widths, "webp")}
            sizes={sizes}
            width={HOVER.width}
            height={HOVER.height}
            alt="AEGIS, a round white robot with a glowing LED face, holding a glass tablet that shows the gateway's live request rate"
            className="block h-auto w-full"
            fetchPriority={priority ? "high" : "auto"}
            loading={priority ? "eager" : "lazy"}
            decoding="async"
            draggable={false}
          />
        </picture>
        <svg
          viewBox={`0 0 ${HOVER.width} ${HOVER.height}`}
          className="absolute inset-0 h-full w-full"
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
        >
          <RobotFace mood={mood} />
          <TabletChart />
        </svg>
      </motion.div>
      <motion.div
        aria-hidden="true"
        className="mx-auto -mt-[2%] h-[5%] w-[46%] rounded-[50%]"
        style={{ scale: shadowScale, opacity: shadowOpacity, background: "radial-gradient(closest-side, var(--robot-shadow), transparent)", translateX: "6%" }}
      />
    </div>
  );
}
