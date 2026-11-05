import { create } from "zustand";
import { useEffect, useState } from "react";

export const EASE_UI = [0.32, 0.72, 0, 1] as const;
export const DUR = { micro: 0.12, std: 0.2, large: 0.32 };
export const SPRING = { type: "spring", stiffness: 260, damping: 26, mass: 0.9 } as const;
export const SPRING_SOFT = { type: "spring", stiffness: 120, damping: 18 } as const;

/** The visitor's "Pause motion" choice (nav), remembered per browser. */
export const useMotionPause = create<{ paused: boolean; toggle: () => void }>((set, get) => ({
  paused: readPaused(),
  toggle: () => {
    const paused = !get().paused;
    try {
      localStorage.setItem("prahari-motion-paused", paused ? "1" : "0");
    } catch {
      /* storage unavailable: the choice lasts for this visit */
    }
    document.documentElement.dataset.motion = paused ? "off" : "on";
    set({ paused });
  },
}));

function readPaused(): boolean {
  try {
    const paused = typeof localStorage !== "undefined" && localStorage.getItem("prahari-motion-paused") === "1";
    if (paused && typeof document !== "undefined") document.documentElement.dataset.motion = "off";
    return paused;
  } catch {
    return false;
  }
}

/** True when the OS asks for reduced motion or the visitor paused motion. */
export function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  const paused = useMotionPause((s) => s.paused);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const fn = () => setReduced(mq.matches);
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, []);
  return reduced || paused;
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

/** Runs `fn` once, on the visitor's first interaction (pointer, touch, scroll or key) or after
 *  `fallbackMs`, whichever comes first. Used for work the first screen does not need, so it never
 *  competes with the first paint. Returns a cancel function. */
export function onFirstInteraction(fn: () => void, fallbackMs: number): () => void {
  const events = ["pointermove", "pointerdown", "touchstart", "scroll", "keydown", "wheel"] as const;
  let done = false;
  const run = () => {
    if (done) return;
    done = true;
    cancel();
    fn();
  };
  const timer = window.setTimeout(run, fallbackMs);
  const cancel = () => {
    window.clearTimeout(timer);
    events.forEach((e) => window.removeEventListener(e, run));
  };
  events.forEach((e) => window.addEventListener(e, run, { once: true, passive: true }));
  return () => {
    done = true;
    cancel();
  };
}
