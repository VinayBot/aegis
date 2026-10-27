// AEGIS, docked in the bottom-right corner on every page: a small 3D robot (scenes/Robot3D)
// hovering over a glowing pad. It never flies around the page.
//   rest:   gentle hover, sleepy eyes, a bubble that cycles a few things to ask
//   hover:  it perks up: eyes widen, head turns to you, a little wiggle
//   click:  it wakes (eyes flash, thrusters burst, a hop) and the chat opens
// Clicking a suggestion in the bubble asks AEGIS that question for real. With reduced motion it
// stays still and opens on click. Below laptop width it is smaller and the bubble is hidden, so it never
// sits over content.
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { onFirstInteraction, useReducedMotion } from "../lib/motion";

const Robot3D = lazy(() => import("../scenes/Robot3D"));

const W = 116; // the canvas also holds the halo and thruster flames
const H = 150;

const PROMPTS = [
  "Ask me anything",
  "How does failover work?",
  "What is the cache hit rate right now?",
  "Why is busy not broken?",
  "Is the primary healthy?",
];

export default function AssistantBot({ open, onOpen }: { open: boolean; onOpen: (question?: string) => void }) {
  const reduced = useReducedMotion();
  const controls = useAnimationControls();
  const [hover, setHover] = useState(false);
  const [waking, setWaking] = useState(false);
  const [mount, setMount] = useState(false);
  const [ready, setReady] = useState(false);
  const [prompt, setPrompt] = useState(0);
  const pointer = useRef<[number, number]>([0, 0]);
  const self = useRef<HTMLDivElement>(null);

  // The 3D robot (three.js) loads on the first interaction, or after 6 s, never during the first paint.
  useEffect(() => onFirstInteraction(() => setMount(true), 6000), []);

  // Its head follows the pointer.
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const r = self.current?.getBoundingClientRect();
      if (!r) return;
      pointer.current = [(e.clientX - (r.left + r.width / 2)) / (window.innerWidth / 2), (e.clientY - (r.top + r.height / 3)) / (window.innerHeight / 2)];
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, []);

  // The bubble cycles suggestions while nobody is interacting.
  useEffect(() => {
    if (reduced || hover || open) return;
    const t = window.setInterval(() => setPrompt((n) => (n + 1) % PROMPTS.length), 5200);
    return () => window.clearInterval(t);
  }, [reduced, hover, open]);

  const wake = async (question?: string) => {
    if (reduced) {
      onOpen(question);
      return;
    }
    if (waking) return;
    setWaking(true);
    await controls.start({ y: [0, -22, -14, -26, 0], scale: [1, 1.08, 1, 1.05, 1], rotate: [0, -6, 6, -3, 0], transition: { duration: 0.9, ease: "easeOut" } });
    setWaking(false);
    onOpen(question);
  };

  if (open) return null;
  const text = hover ? "Hi! Click me to chat." : PROMPTS[prompt];
  const question = !hover && prompt > 0 ? PROMPTS[prompt] : undefined;

  return (
    <div ref={self} className="fixed bottom-3 right-3 z-50 flex origin-bottom-right scale-75 flex-col items-end lg:scale-100">
      <AnimatePresence mode="wait">
        {(ready || reduced) && (
          <motion.button key={text} type="button" onClick={() => void wake(question)}
            initial={reduced ? false : { opacity: 0, y: 6, scale: 0.92, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }} exit={{ opacity: 0, y: -4, scale: 0.96, filter: "blur(4px)" }}
            transition={{ type: "spring", stiffness: 320, damping: 26 }}
            className="bot-bubble glass glass-strong glow-border is-live mr-2 hidden max-w-[240px] lg:block px-3.5 py-2 text-left text-[0.85rem] font-semibold text-ink"
            tabIndex={-1} aria-hidden="true">
            {text}
          </motion.button>
        )}
      </AnimatePresence>
      <motion.button
        type="button"
        animate={controls}
        onClick={() => void wake()}
        onPointerEnter={() => setHover(true)}
        onPointerLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        aria-label="Ask AEGIS about this project (press / or Ctrl+K)"
        className="relative block cursor-pointer rounded-full focus-visible:outline-offset-4"
        style={{ width: W, height: H }}
      >
        <span aria-hidden="true" className="bot-ping" />
        <span aria-hidden="true" className={`bot-pad ${hover ? "bot-pad-hot" : ""}`} />
        <motion.span className="absolute inset-0 block" animate={!reduced ? { y: [0, -6, 0] } : undefined} transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}>
          <motion.span className="absolute inset-0 block" animate={hover && !waking && !reduced ? { rotate: [0, -6, 5, -2, 0] } : { rotate: 0 }}
            transition={{ duration: 0.8, ease: "easeInOut" }} style={{ transformOrigin: "50% 75%" }}>
            {mount && (
              <Suspense fallback={null}>
                <Robot3D mood={waking ? "boot" : hover ? "awake" : "sleep"} flying={waking} pointer={pointer} still={reduced} onReady={() => setReady(true)} />
              </Suspense>
            )}
          </motion.span>
        </motion.span>
      </motion.button>
    </div>
  );
}
