// AEGIS as a little flying robot, bottom-right on every page.
//   arrive: it flies in from the left on its boosters, banking into the turn
//   rest:   it lands at the right, boosters off, eyes dimmed, with an "Ask me anything" bubble
//   patrol: now and then it hops out to the left and back
//   wake:   clicked, its eyes power on, the boosters fire, it hops, and the chat opens
// With reduced motion it simply sits there and opens on click.
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import CuteBot from "./CuteBot";
import { useReducedMotion } from "../lib/motion";

type Phase = "hidden" | "arriving" | "resting" | "patrol" | "waking";

const W = 92; // rendered width in px
const H = Math.round(W * (214 / 220));

export default function FlyingBot({ open, onOpen }: { open: boolean; onOpen: () => void }) {
  const reduced = useReducedMotion();
  const controls = useAnimationControls();
  const [phase, setPhase] = useState<Phase>(reduced ? "resting" : "hidden");
  const [hover, setHover] = useState(false);
  const busy = useRef(false);

  const fly = useCallback(async (from: "left-edge" | "patrol" | "return") => {
    if (reduced || busy.current) return;
    busy.current = true;
    const vw = window.innerWidth;
    if (from === "left-edge") {
      setPhase("arriving");
      await controls.set({ x: -vw + 40, y: -220, rotate: 14, opacity: 1 });
      await controls.start({
        x: [-vw + 40, -vw * 0.55, -vw * 0.2, 0],
        y: [-220, -90, -160, 0],
        rotate: [14, 10, 6, 0],
        transition: { duration: 3.4, ease: [0.45, 0, 0.2, 1], times: [0, 0.35, 0.7, 1] },
      });
    } else if (from === "return") {
      setPhase("arriving");
      await controls.set({ x: 0, y: 160, rotate: 0, opacity: 1 });
      await controls.start({ y: [160, -40, 0], transition: { duration: 1.1, ease: [0.32, 0.72, 0, 1] } });
    } else {
      setPhase("patrol");
      await controls.start({ x: [0, -40, -230, -230, -60, 0], y: [0, -60, -110, -100, -50, 0], rotate: [0, -10, -12, 10, 8, 0],
        transition: { duration: 4.2, ease: "easeInOut", times: [0, 0.15, 0.45, 0.55, 0.85, 1] } });
    }
    setPhase("resting");
    busy.current = false;
  }, [controls, reduced]);

  // Arrive once, shortly after the page loads.
  useEffect(() => {
    if (reduced) return;
    const t = window.setTimeout(() => void fly("left-edge"), 1200);
    return () => window.clearTimeout(t);
  }, [fly, reduced]);

  // An occasional patrol while resting (never while hovered or chatting).
  useEffect(() => {
    if (reduced || open) return;
    const t = window.setInterval(() => {
      if (!hover && !document.hidden && phase === "resting") void fly("patrol");
    }, 28000);
    return () => window.clearInterval(t);
  }, [fly, hover, open, phase, reduced]);

  // When the chat closes, fly back up into the resting spot.
  const wasOpen = useRef(open);
  useEffect(() => {
    if (wasOpen.current && !open) void fly("return");
    wasOpen.current = open;
  }, [open, fly]);

  const wake = async () => {
    if (reduced) {
      onOpen();
      return;
    }
    if (busy.current) return;
    busy.current = true;
    setPhase("waking");
    await controls.start({ y: [0, -34, -26, -40], rotate: [0, -4, 4, 0], scale: [1, 1.06, 1, 1.04], transition: { duration: 0.85, ease: "easeOut" } });
    busy.current = false;
    onOpen();
    void controls.set({ y: 0, scale: 1 });
  };

  if (open) return null;
  const flying = phase === "arriving" || phase === "patrol" || phase === "waking";
  const bubble = phase === "resting" ? (hover ? "I'm awake now. Click to chat." : "Ask me anything") : phase === "waking" ? "Powering up…" : null;

  return (
    <div className="fixed bottom-12 right-6 z-50" style={{ width: W }}>
      <AnimatePresence>
        {bubble && (
          <motion.button key={bubble} type="button" onClick={() => void wake()}
            initial={reduced ? false : { opacity: 0, y: 8, scale: 0.9 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6, scale: 0.95 }}
            transition={{ type: "spring", stiffness: 300, damping: 24 }}
            className="bot-bubble glass glass-strong glow-border absolute bottom-[calc(100%+6px)] right-0 whitespace-nowrap px-3.5 py-2 text-[0.85rem] font-semibold text-ink"
            aria-hidden="true" tabIndex={-1}>
            {bubble}
          </motion.button>
        )}
      </AnimatePresence>
      <motion.button
        type="button"
        animate={controls}
        initial={reduced ? false : { opacity: 0 }}
        onClick={() => void wake()}
        onPointerEnter={() => setHover(true)}
        onPointerLeave={() => setHover(false)}
        aria-label="Ask AEGIS about this project (press / or Ctrl+K)"
        className="relative block cursor-pointer focus-visible:outline-offset-4"
        style={{ width: W, height: H }}
      >
        <motion.span className="absolute inset-0 block" animate={!flying && !reduced ? { y: [0, -7, 0] } : { y: 0 }}
          transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}>
          <span aria-hidden="true" className={`pod-flame pod-flame-l ${flying ? "pod-full" : ""}`}><span className="jetpack-core" /></span>
          <span aria-hidden="true" className={`pod-flame pod-flame-r ${flying ? "pod-full" : ""}`}><span className="jetpack-core" /></span>
          <motion.span className="absolute inset-0 block" animate={hover && !flying && !reduced ? { rotate: [0, -7, 5, -3, 0] } : { rotate: 0 }}
            transition={{ duration: 0.9, ease: "easeInOut" }} style={{ transformOrigin: "50% 80%" }}>
            <CuteBot mood={phase === "waking" ? "boot" : flying || hover ? "awake" : "sleep"} className="relative h-full w-full drop-shadow-[0_10px_18px_rgba(0,0,0,0.6)]" />
          </motion.span>
        </motion.span>
        <span aria-hidden="true" className={`bot-pad ${flying ? "bot-pad-off" : ""}`} style={{ bottom: -30 }} />
      </motion.button>
    </div>
  );
}
