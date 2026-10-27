// An edge that can carry packets. Packets are SVG circles animated along the edge's own path
// with requestAnimationFrame, created and removed imperatively (see packets.ts).
import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";
import { useEffect, useRef } from "react";
import { registry, type PacketKind } from "./packets";

const PACKET: Record<PacketKind, { fill: string; r: number; fade?: boolean }> = {
  normal: { fill: "var(--cyan)", r: 5 },
  cached: { fill: "var(--violet)", r: 5 },
  hedge: { fill: "var(--amber)", r: 5 },
  rejected: { fill: "var(--red)", r: 5.5 },
  loser: { fill: "var(--amber)", r: 4.5, fade: true },
  probe: { fill: "var(--cyan-hi)", r: 4 },
};

// getPointAtLength is slow and was the hottest call during bursts (one per packet per frame), so
// each edge's path is sampled once per shape and positions are interpolated from the samples.
const SAMPLES = 64;
const sampled = new WeakMap<SVGPathElement, { d: string; xy: Float32Array }>();

function pointOn(path: SVGPathElement, frac: number): { x: number; y: number } {
  const d = path.getAttribute("d") ?? "";
  let cache = sampled.get(path);
  if (!cache || cache.d !== d) {
    const len = path.getTotalLength();
    const xy = new Float32Array((SAMPLES + 1) * 2);
    for (let i = 0; i <= SAMPLES; i++) {
      const pt = path.getPointAtLength((len * i) / SAMPLES);
      xy[i * 2] = pt.x;
      xy[i * 2 + 1] = pt.y;
    }
    cache = { d, xy };
    sampled.set(path, cache);
  }
  const f = Math.min(1, Math.max(0, frac)) * SAMPLES;
  const i = Math.min(SAMPLES - 1, Math.floor(f));
  const k = f - i;
  const { xy } = cache;
  return { x: xy[i * 2] + (xy[i * 2 + 2] - xy[i * 2]) * k, y: xy[i * 2 + 1] + (xy[i * 2 + 3] - xy[i * 2 + 1]) * k };
}

const STROKE: Record<string, string> = {
  flow: "var(--edge)",
  hedge: "var(--amber)",
  cache: "var(--violet)",
  coalesce: "var(--violet)",
  response: "var(--cyan-deep)",
  side: "var(--edge)",
};

/** Where a packet lands: a ripple ring and a small burst of sparks. */
function arrive(g: SVGGElement, at: { x: number; y: number }, fill: string) {
  const ns = "http://www.w3.org/2000/svg";
  const ring = document.createElementNS(ns, "circle");
  ring.setAttribute("cx", String(at.x));
  ring.setAttribute("cy", String(at.y));
  ring.style.fill = "none";
  ring.style.stroke = fill;
  ring.style.strokeWidth = "2";
  g.appendChild(ring);
  const sparks = Array.from({ length: 7 }, (_, i) => {
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("r", "1.8");
    c.style.fill = fill;
    g.appendChild(c);
    return { c, a: (i / 7) * Math.PI * 2 + i * 0.3 };
  });
  const start = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / 520);
    const e = 1 - Math.pow(1 - t, 3);
    ring.setAttribute("r", String(4 + e * 26));
    ring.style.opacity = String(1 - t);
    for (const s of sparks) {
      s.c.setAttribute("cx", String(at.x + Math.cos(s.a) * e * 22));
      s.c.setAttribute("cy", String(at.y + Math.sin(s.a) * e * 22));
      s.c.style.opacity = String(1 - t);
    }
    if (t < 1) requestAnimationFrame(step);
    else {
      ring.remove();
      sparks.forEach((s) => s.c.remove());
    }
  };
  requestAnimationFrame(step);
}

export default function PulseEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const [path] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.28 });
  const pathRef = useRef<SVGPathElement>(null);
  const layer = useRef<SVGGElement>(null);
  const kind = (data?.kind as string) ?? "flow";
  const broken = data?.status === "broken";
  const dimmed = Boolean(data?.dimmed);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return registry.edge(id, {
      launch(packetKind, durationMs) {
        const p = pathRef.current;
        const g = layer.current;
        if (!p || !g) return;
        const style = PACKET[packetKind];
        // a comet: a bright head and a fading tail of five trailing dots
        const parts: SVGCircleElement[] = [];
        for (let i = 0; i < 6; i++) {
          const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          c.setAttribute("r", String(Math.max(1.4, style.r - i * 0.8)));
          c.style.fill = style.fill;
          c.style.opacity = String(1 - i * 0.17);
          if (i === 0) {
            c.setAttribute("r", String(style.r + 3));
            c.style.opacity = "0.35";
          }
          g.appendChild(c);
          parts.push(c);
        }
        const dot = parts[0];
        const trail: { x: number; y: number }[] = [];
        if (reduced) {
          // Reduced motion: no travel; the packet blinks at the destination end instead.
          const end = pointOn(p, 1);
          dot.setAttribute("cx", String(end.x));
          dot.setAttribute("cy", String(end.y));
          parts.slice(1).forEach((c) => c.remove());
          window.setTimeout(() => dot.remove(), 600);
          return;
        }
        const start = performance.now();
        const step = (now: number) => {
          const t = Math.min(1, (now - start) / durationMs);
          const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
          const limit = style.fade ? 0.55 : 1;
          trail.unshift(pointOn(p, eased * limit));
          if (trail.length > 12) trail.pop();
          parts.forEach((c, i) => {
            const pt = trail[Math.min(trail.length - 1, i === 0 ? 0 : i * 2 - 1)];
            c.setAttribute("cx", String(pt.x));
            c.setAttribute("cy", String(pt.y));
            if (style.fade) c.style.opacity = String((1 - t) * (1 - i * 0.17));
          });
          if (t < 1) requestAnimationFrame(step);
          else {
            parts.forEach((c) => c.remove());
            if (!style.fade) arrive(g, pointOn(p, 1), style.fill);
          }
        };
        requestAnimationFrame(step);
      },
    });
  }, [id]);

  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: broken ? "var(--red)" : STROKE[kind] ?? "var(--line)",
          strokeWidth: broken ? 2 : kind === "flow" ? 1.6 : 1.4,
          strokeDasharray: broken ? "4 7" : kind === "side" || kind === "hedge" || kind === "cache" || kind === "coalesce" ? "5 6" : undefined,
          opacity: dimmed ? 0.12 : kind === "flow" || kind === "side" ? 1 : 0.55,
          animation: broken ? "edge-flicker 0.9s steps(2) infinite" : undefined,
          transition: "opacity 200ms",
        }}
      />
      {!broken && (kind === "flow" || kind === "response") && (
        <path d={path} fill="none" className="flow-dash" style={{ stroke: kind === "response" ? "var(--cyan)" : "var(--violet)", strokeWidth: 1.4, opacity: dimmed ? 0.08 : 0.55 }} />
      )}
      <path ref={pathRef} d={path} fill="none" stroke="none" />
      <g ref={layer} />
    </>
  );
}
