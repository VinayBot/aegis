// The standing robot in its starfield, full-bleed. Its eye rings glow and occasionally blink, and
// it breathes (a 1.5% scale) unless the viewer prefers reduced motion.
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { ASSETS, STANDING, srcSet } from "../config/robot";
import { useReducedMotion } from "../lib/motion";

export default function StandingRobot({ excited = false }: { excited?: boolean }) {
  const reduced = useReducedMotion();
  const [blink, setBlink] = useState(false);
  useEffect(() => {
    if (reduced) return;
    const id = window.setInterval(() => {
      setBlink(true);
      window.setTimeout(() => setBlink(false), 150);
    }, 4300);
    return () => window.clearInterval(id);
  }, [reduced]);
  const { width, height, eyes, visor } = STANDING.scene;
  return (
    <motion.div
      className="relative w-full overflow-hidden"
      style={{ aspectRatio: `${width} / ${height}`, transformOrigin: "50% 90%" }}
      animate={reduced ? undefined : { scale: [1, 1.015, 1] }}
      transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
    >
      <picture>
        <source type="image/avif" srcSet={srcSet(ASSETS.scene.base, ASSETS.scene.widths, "avif")} sizes="(max-width: 900px) 100vw, 50vw" />
        <img
          src={`${ASSETS.scene.base}-800.webp`}
          srcSet={srcSet(ASSETS.scene.base, ASSETS.scene.widths, "webp")}
          sizes="(max-width: 900px) 100vw, 50vw"
          width={width}
          height={height}
          loading="lazy"
          decoding="async"
          alt="PRAHARI standing in a starfield, a small white robot with a dark visor and two glowing cyan eye rings"
          className="block h-full w-full object-cover"
        />
      </picture>
      <svg viewBox={`0 0 ${width} ${height}`} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
        <defs>
          <filter id="eye-bloom" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="10" />
          </filter>
        </defs>
        {[eyes.left, eyes.right].map((e, i) => (
          <g key={i}>
            <motion.circle
              cx={e.cx}
              cy={e.cy}
              r={e.r}
              fill="none"
              strokeWidth={14}
              style={{ stroke: "var(--cyan)" }}
              filter="url(#eye-bloom)"
              animate={reduced ? { opacity: 0.7 } : { opacity: excited ? [0.8, 1, 0.8] : [0.35, 0.8, 0.35] }}
              transition={{ duration: excited ? 1.2 : 3.2, repeat: Infinity, ease: "easeInOut" }}
            />
            <motion.ellipse
              cx={e.cx}
              cy={e.cy}
              rx={e.r + 10}
              ry={e.r + 10}
              style={{ fill: visor, transformOrigin: `${e.cx}px ${e.cy - e.r - 10}px`, transformBox: "view-box" }}
              initial={{ scaleY: 0 }}
              animate={{ scaleY: blink ? 1 : 0 }}
              transition={{ duration: 0.07 }}
            />
          </g>
        ))}
      </svg>
    </motion.div>
  );
}
