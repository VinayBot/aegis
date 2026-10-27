import { useEffect, useState } from "react";

export const EASE_UI = [0.32, 0.72, 0, 1] as const;
export const DUR = { micro: 0.12, std: 0.2, large: 0.32 };
export const SPRING = { type: "spring", stiffness: 260, damping: 26, mass: 0.9 } as const;
export const SPRING_SOFT = { type: "spring", stiffness: 120, damping: 18 } as const;

export function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const fn = () => setReduced(mq.matches);
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, []);
  return reduced;
}

/** True once the element has come within `margin` of the viewport; used to mount heavy scenes late. */
export function useNearViewport<T extends Element>(ref: React.RefObject<T | null>, margin = "600px"): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setNear(true);
    }, { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, margin, near]);
  return near;
}

/** Whether the element is on screen right now (pauses animation loops when it is not). */
export function useOnScreen<T extends Element>(ref: React.RefObject<T | null>): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => setOn(entries.some((e) => e.isIntersecting)));
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);
  return on;
}
