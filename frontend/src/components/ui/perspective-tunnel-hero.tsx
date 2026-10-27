// A 3D corridor of cards that fly toward you as you scroll (adapted from 21st.dev's
// PerspectiveTunnelHero). Changes for this site:
//   - driven by the page's own scroll (a tall section with a sticky stage), so the mouse wheel,
//     keyboard and screen readers scroll normally; the original hijacked the wheel;
//   - each card is a real picture of one of the site's pages and a link to it;
//   - motion comes from `motion/react` (the same library as framer-motion, already in the app);
//   - colours and type come from the design tokens; reduced motion shows a still grid.
import { motion, useMotionValue, useScroll, useSpring, useTransform, type MotionValue } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";
import { useReducedMotion } from "@/lib/motion";

export interface TunnelItem {
  /** Picture base path; `-480` / `-800` and `.avif` / `.webp` are appended. */
  src: string;
  title: string;
  about: string;
  to: string;
}

interface Placed {
  item: TunnelItem;
  angleCos: number;
  angleSin: number;
  radiusFactorX: number;
  radiusFactorY: number;
  rotationZ: number;
  index: number;
}

function place(items: TunnelItem[]): Placed[] {
  return items.map((item, i) => {
    const golden = i * 137.5 * (Math.PI / 180);
    return {
      item,
      angleCos: Math.cos(golden),
      angleSin: Math.sin(golden),
      radiusFactorX: 0.28 + (i % 3) * 0.08,
      radiusFactorY: 0.26 + (i % 2) * 0.09,
      rotationZ: (i % 2 === 0 ? 1 : -1) * (3 + (i % 5) * 2),
      index: i,
    };
  });
}

const SPRING = { stiffness: 60, damping: 25, mass: 0.5 };

function useCameraParallax(enabled: boolean) {
  const rawX = useMotionValue(0);
  const rawY = useMotionValue(0);
  const x = useSpring(rawX, SPRING);
  const y = useSpring(rawY, SPRING);
  useEffect(() => {
    if (!enabled) return;
    const onMove = (e: PointerEvent) => {
      rawX.set((e.clientX / window.innerWidth - 0.5) * 2);
      rawY.set((e.clientY / window.innerHeight - 0.5) * 2);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [enabled, rawX, rawY]);
  return { x, y };
}

interface Stage { width: number; height: number; isMobile: boolean }

function Picture({ src, alt, className }: { src: string; alt: string; className?: string }) {
  return (
    <picture>
      <source type="image/avif" srcSet={`${src}-480.avif 480w, ${src}-800.avif 800w`} sizes="(max-width: 768px) 40vw, 18vw" />
      <img src={`${src}-480.webp`} srcSet={`${src}-480.webp 480w, ${src}-800.webp 800w`} sizes="(max-width: 768px) 40vw, 18vw"
        alt={alt} width={480} height={300} loading="lazy" decoding="async" draggable={false} className={className} />
    </picture>
  );
}

function Card({ card, progress, pointer, total, stage }: { card: Placed; progress: MotionValue<number>; pointer: { x: MotionValue<number>; y: MotionValue<number> }; total: number; stage: Stage }) {
  const span = 0.55;
  const start = (card.index / total) * (1 - span);
  const local = useTransform(progress, [start, start + span], [0, 1]);
  const z = useTransform(local, [0, 1], [stage.isMobile ? -1200 : -1700, stage.isMobile ? 350 : 500]);
  const opacity = useTransform(local, [0, 0.18, 0.82, 0.98], [0, 1, 1, 0]);
  const width = stage.isMobile ? Math.max(150, Math.min(200, stage.width * 0.42)) : Math.max(220, Math.min(320, stage.width * 0.2));
  const baseX = card.angleCos * stage.width * card.radiusFactorX;
  const baseY = card.angleSin * stage.height * card.radiusFactorY;
  const pan = stage.isMobile ? 20 : 45;
  const transform = useTransform([z, pointer.x, pointer.y], ([cz, px, py]: number[]) =>
    `translate3d(calc(-50% + ${baseX - px * pan}px), calc(-50% + ${baseY - py * pan}px), ${cz}px) rotateZ(${card.rotationZ}deg)`);
  return (
    <motion.div style={{ width, transform, opacity, zIndex: card.index + 1 }} className="pointer-events-auto absolute left-1/2 top-1/2 aspect-[16/10] will-change-transform">
      <Link to={card.item.to} className="tunnel-card group relative block h-full w-full overflow-hidden rounded-2xl">
        <Picture src={card.item.src} alt={`${card.item.title}: ${card.item.about}`} className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-110" />
        <span className="tunnel-scrim absolute inset-0 flex flex-col justify-end p-4">
          <span className="font-display text-lg font-semibold text-ink">{card.item.title}</span>
          <span className="text-xs text-ink-soft">{card.item.about}</span>
        </span>
      </Link>
    </motion.div>
  );
}

export default function PerspectiveTunnelHero({ items, title, subtitle, className }: { items: TunnelItem[]; title: string; subtitle: string; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const [stage, setStage] = useState<Stage>({ width: 1440, height: 900, isMobile: false });
  useEffect(() => {
    const update = () => setStage({ width: window.innerWidth, height: window.innerHeight, isMobile: window.innerWidth < 768 });
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  const progress = useSpring(scrollYProgress, { stiffness: 80, damping: 30, restDelta: 0.0001 });
  const pointer = useCameraParallax(!reduced);
  const titleScale = useTransform(progress, [0, 0.5, 1], [0.8, 1, 1.3]);
  const titleOpacity = useTransform(progress, [0, 0.12, 0.85, 1], [0.35, 1, 1, 0]);
  const promptOpacity = useTransform(progress, [0, 0.12], [1, 0]);
  const cards = place(items);

  if (reduced) {
    return (
      <section aria-labelledby="tunnel-title" className={cn("mx-auto max-w-[1440px] px-[var(--gutter)] py-24", className)}>
        <h2 id="tunnel-title" className="font-display text-4xl font-semibold tracking-tight md:text-6xl">{title}</h2>
        <p className="mt-4 max-w-[48ch] text-lg text-ink-soft">{subtitle}</p>
        <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((it) => (
            <li key={it.to + it.title}>
              <Link to={it.to} className="tunnel-card block overflow-hidden rounded-2xl">
                <Picture src={it.src} alt="" className="aspect-[16/10] w-full object-cover" />
                <span className="block p-3"><span className="font-semibold text-ink">{it.title}</span><span className="block text-sm text-ink-mute">{it.about}</span></span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return (
    <section ref={ref} aria-labelledby="tunnel-title" className={cn("relative", className)} style={{ height: "320vh" }}>
      <div className="sticky top-0 flex h-[100svh] items-center justify-center overflow-hidden" style={{ perspective: stage.isMobile ? "900px" : "1200px", transformStyle: "preserve-3d" }}>
        <div aria-hidden="true" className="tunnel-glow pointer-events-none absolute inset-0" />
        <div aria-hidden="true" className="tunnel-ring pointer-events-none absolute h-[48vmin] w-[48vmin] rounded-full" />
        <div aria-hidden="true" className="tunnel-ring tunnel-ring-dashed pointer-events-none absolute h-[76vmin] w-[76vmin] rounded-full" />
        <motion.div style={{ scale: titleScale, opacity: titleOpacity }} className="pointer-events-none z-0 flex max-w-xl flex-col items-center px-6 text-center">
          <h2 id="tunnel-title" className="text-shimmer font-display text-5xl font-bold leading-[0.95] tracking-tight sm:text-7xl md:text-8xl">{title}</h2>
          <p className="mt-5 max-w-[36ch] text-base text-ink-soft">{subtitle}</p>
        </motion.div>
        <motion.p style={{ opacity: promptOpacity }} className="pointer-events-none absolute bottom-10 z-20 text-sm text-ink-mute">
          Keep scrolling to fly through the deck
        </motion.p>
        <div className="pointer-events-none absolute inset-0" style={{ transformStyle: "preserve-3d" }}>
          {cards.map((card) => (
            <Card key={card.index} card={card} progress={progress} pointer={pointer} total={cards.length} stage={stage} />
          ))}
        </div>
      </div>
    </section>
  );
}
