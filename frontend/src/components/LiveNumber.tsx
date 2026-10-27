// A number that counts to its new value only when the value meaningfully changes, never on every
// poll. Reduced motion: it simply updates.
import { animate, useMotionValue, useTransform, motion } from "motion/react";
import { useEffect, useRef } from "react";
import { useReducedMotion } from "../lib/motion";

export default function LiveNumber({
  value,
  format,
  threshold = 0.005,
  className = "",
}: {
  value: number | null | undefined;
  format: (n: number) => string;
  threshold?: number;
  className?: string;
}) {
  const reduced = useReducedMotion();
  const mv = useMotionValue(value ?? 0);
  const shown = useTransform(mv, (v) => format(v));
  const last = useRef<number | null>(value ?? null);
  useEffect(() => {
    if (value === null || value === undefined) return;
    const prev = last.current;
    const changed = prev === null || Math.abs(value - prev) > Math.max(threshold * Math.abs(prev), 1e-12);
    if (!changed) return;
    last.current = value;
    if (reduced || prev === null) {
      mv.set(value);
      return;
    }
    const c = animate(mv, value, { duration: 0.7, ease: [0.32, 0.72, 0, 1] });
    return () => c.stop();
  }, [value, reduced, mv, threshold]);
  if (value === null || value === undefined) return <span className={className}>—</span>;
  return <motion.span className={`font-tabular ${className}`}>{shown}</motion.span>;
}
