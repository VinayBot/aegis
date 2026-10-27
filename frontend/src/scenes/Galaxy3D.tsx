// The Architecture page's 3D view: the gateway as a small galaxy.
//   core     the AEGIS gateway itself, a glowing star with orbit rings
//   planets  the six phases every request passes through, in order round the core
//   moons    each model backend circles "Think", coloured by its health and breaker
//   comets   every real request is a comet that flies planet to planet as its events arrive:
//            a cache hit jumps from Remember straight to Deliver in violet, a rejection bursts
//            red where it was stopped, a hedge or failover burns amber
// Driven only by /ws/events (or the replay fixture); with no events, nothing travels.
import { OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { memo, useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { groupOf } from "../components/arch/layout";
import { JOURNEY } from "../components/arch/meta";
import { onEvent } from "../lib/live";
import { mulberry32 } from "../lib/rng";
import type { ArchNode, GatewayEvent } from "../lib/types";
import { dotTexture } from "./sprite";

// WebGL cannot read CSS tokens; restated from tokens.css.
const COL = {
  cyan: new THREE.Color("#74d7f7"),
  cyanHi: new THREE.Color("#b8ecff"),
  violet: new THREE.Color("#c4a2f5"),
  amber: new THREE.Color("#ffc25c"),
  red: new THREE.Color("#ff6b7a"),
  white: new THREE.Color("#eaf0ff"),
  planet: new THREE.Color("#1b2a4a"),
};

const PHASES = JOURNEY.map((j) => j.group); // Ingress, Policy, Memory, Reflexes, Backends, Delivery
const RADIUS = 5.2;

function planetPos(i: number, t: number): THREE.Vector3 {
  const a = (i / PHASES.length) * Math.PI * 2 + t * 0.03;
  return new THREE.Vector3(Math.cos(a) * RADIUS, Math.sin(a * 2) * 0.35, Math.sin(a) * RADIUS * 0.62);
}

interface Comet {
  id: string;
  from: THREE.Vector3;
  to: number; // phase index, -1 = core
  t: number;
  dur: number;
  color: THREE.Color;
  trail: THREE.Vector3[];
  dead: number; // >0: bursting
  done: boolean;
}

const MAX_COMETS = 48;
const TRAIL = 10;

type Labels = React.RefObject<Record<string, HTMLSpanElement | null>>;
const at = new THREE.Vector3();

/** Pins a DOM label to a point `dy` above or below an object, as projected this frame. */
function pin(el: HTMLSpanElement | null | undefined, obj: THREE.Object3D | null | undefined, dy: number, camera: THREE.Camera, w: number, h: number) {
  if (!el || !obj) return;
  obj.localToWorld(at.set(0, dy, 0)).project(camera);
  el.style.visibility = at.z > 1 ? "hidden" : "visible";
  el.style.transform = `translate3d(${((at.x + 1) / 2) * w}px, ${((1 - at.y) / 2) * h}px, 0) translate(-50%, -50%)`;
}

/** Re-rendered only when the set of backends changes; live status is read from `live` each frame,
 *  so the topology poll never remounts the scene or its labels. */
const Scene = memo(function Scene({ backends, live, flash, labels }: { backends: { id: string; label: string }[]; live: React.RefObject<ArchNode[]>; flash: React.RefObject<Record<string, number>>; labels: Labels }) {
  const time = useRef(0);
  const planets = useRef<(THREE.Mesh | null)[]>([]);
  const halos = useRef<(THREE.Mesh | null)[]>([]);
  const moons = useRef<(THREE.Mesh | null)[]>([]);
  const core = useRef<THREE.Mesh>(null);
  const comets = useRef(new Map<string, Comet>());
  const trailGeo = useRef<THREE.BufferGeometry>(null);
  const trailPos = useMemo(() => new Float32Array(MAX_COMETS * TRAIL * 3).fill(-999), []);
  const trailCol = useMemo(() => new Float32Array(MAX_COMETS * TRAIL * 3), []);

  // Background stars, seeded.
  const stars = useMemo(() => {
    const rnd = mulberry32(11);
    const a = new Float32Array(1400 * 3);
    for (let i = 0; i < 1400; i++) {
      const r = 18 + rnd() * 30;
      const th = rnd() * Math.PI * 2;
      const ph = Math.acos(2 * rnd() - 1);
      a.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph) * 0.6, r * Math.sin(ph) * Math.sin(th)], i * 3);
    }
    return a;
  }, []);

  // Orbit path, drawn once.
  const orbit = useMemo(() => {
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= 160; i++) pts.push(planetPos((i / 160) * PHASES.length, 0));
    return new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: COL.cyan, transparent: true, opacity: 0.25 }));
  }, []);

  // Requests become comets.
  useEffect(() => onEvent((e: GatewayEvent) => {
    const id = e.request_id;
    if (!id) return;
    const map = comets.current;
    const t = time.current;
    if (e.type === "request.start") {
      if (map.size >= MAX_COMETS) return;
      map.set(id, { id, from: new THREE.Vector3(0, 0, 0), to: 0, t: 0, dur: 0.9, color: COL.cyanHi.clone(), trail: [], dead: 0, done: false });
      return;
    }
    const c = map.get(id);
    if (!c) return;
    const go = (phase: number, dur = 0.8) => {
      const at = c.to >= 0 ? planetPos(c.to, t) : new THREE.Vector3();
      c.from = c.trail[0]?.clone() ?? at;
      c.to = phase;
      c.t = 0;
      c.dur = dur;
    };
    if (e.type === "stage.enter") {
      const g = groupOf(String(e.stage));
      const i = PHASES.indexOf(g);
      if (i >= 0 && i !== c.to) go(i);
    } else if (e.type === "cache.hit" || e.type === "coalesce.join") {
      c.color = COL.violet.clone();
    } else if (e.type === "hedge.fire" || e.type === "failover") {
      c.color = COL.amber.clone();
    } else if (e.type === "backend.try") {
      go(PHASES.indexOf("Backends"), 0.7);
      flash.current[`backend:${String(e.backend)}`] = t;
    } else if (e.type === "stage.exit" && (e.outcome === "rejected" || e.outcome === "blocked")) {
      c.color = COL.red.clone();
      c.dead = 1;
    } else if (e.type === "request.end") {
      if (e.status !== "ok") {
        c.color = COL.red.clone();
        c.dead = Math.max(c.dead, 1);
      } else {
        go(-1, 1.1);
        c.done = true;
      }
    }
  }), [flash]);

  useFrame((_, dt) => {
    time.current += dt;
    const t = time.current;
    // planets ride their orbit; the one a comet just reached glows
    PHASES.forEach((g, i) => {
      const p = planetPos(i, t);
      planets.current[i]?.position.copy(p);
      const h = halos.current[i];
      if (h) {
        h.position.copy(p);
        const since = t - (flash.current[g] ?? -9);
        const s = 1 + Math.max(0, 0.6 - since) * 1.6;
        h.scale.setScalar(s);
        (h.material as THREE.MeshBasicMaterial).opacity = 0.18 + Math.max(0, 0.6 - since);
      }
    });
    // moons (backends) circle Think
    const think = planetPos(PHASES.indexOf("Backends"), t);
    backends.forEach((meta, i) => {
      const b = live.current.find((n) => n.id === meta.id) ?? (meta as ArchNode);
      const m = moons.current[i];
      if (!m) return;
      const a = t * 0.8 + (i / Math.max(1, backends.length)) * Math.PI * 2;
      m.position.set(think.x + Math.cos(a) * 1.35, think.y + 0.35 + Math.sin(a * 1.3) * 0.2, think.z + Math.sin(a) * 1.35);
      const mat = m.material as THREE.MeshStandardMaterial;
      const open = b.breaker?.state === "open" || b.status === "down";
      const warn = b.breaker?.state === "half_open" || b.status === "degraded";
      const base = open ? COL.red : warn ? COL.amber : b.status === "disabled" ? COL.planet : COL.cyan;
      const since = t - (flash.current[b.id] ?? -9);
      mat.emissive.copy(base).multiplyScalar(0.6 + Math.max(0, 0.8 - since) * 2.5);
    });
    if (core.current) core.current.scale.setScalar(1 + Math.sin(t * 2) * 0.04);

    // comets
    let slot = 0;
    for (const c of comets.current.values()) {
      if (slot >= MAX_COMETS) break;
      let head: THREE.Vector3;
      if (c.dead > 0) {
        c.dead -= dt * 1.4;
        head = c.trail[0]?.clone() ?? new THREE.Vector3();
        if (c.dead <= 0) {
          comets.current.delete(c.id);
          continue;
        }
      } else {
        c.t = Math.min(1, c.t + dt / c.dur);
        const target = c.to >= 0 ? planetPos(c.to, t) : new THREE.Vector3();
        const k = c.t * c.t * (3 - 2 * c.t);
        const mid = c.from.clone().lerp(target, 0.5).add(new THREE.Vector3(0, 1.1, 0));
        head = c.from.clone().lerp(mid, k).lerp(mid.clone().lerp(target, k), k);
        if (c.t >= 1 && c.to >= 0) flash.current[PHASES[c.to]] = t;
        if (c.t >= 1 && c.done) {
          comets.current.delete(c.id);
          continue;
        }
      }
      c.trail.unshift(head);
      if (c.trail.length > TRAIL) c.trail.pop();
      for (let k = 0; k < TRAIL; k++) {
        const p = c.trail[Math.min(k, c.trail.length - 1)];
        const fade = (1 - k / TRAIL) * (c.dead > 0 ? c.dead : 1);
        const burst = c.dead > 0 ? (1 - c.dead) * 0.6 : 0;
        trailPos.set([p.x + (k % 3 - 1) * burst, p.y + ((k >> 1) % 3 - 1) * burst, p.z], (slot * TRAIL + k) * 3);
        trailCol.set([c.color.r * fade, c.color.g * fade, c.color.b * fade], (slot * TRAIL + k) * 3);
      }
      slot++;
    }
    for (let s = slot; s < MAX_COMETS; s++) for (let k = 0; k < TRAIL; k++) trailPos.set([0, -999, 0], (s * TRAIL + k) * 3);
    if (trailGeo.current) {
      trailGeo.current.attributes.position.needsUpdate = true;
      trailGeo.current.attributes.color.needsUpdate = true;
    }
  });

  // One plain DOM layer of labels, moved each frame (drei's Html gives every label its own React root).
  useFrame(({ camera, size }) => {
    const l = labels.current;
    pin(l.core, core.current, -1.35, camera, size.width, size.height);
    planets.current.forEach((m, i) => pin(l[`p${i}`], m, -0.72, camera, size.width, size.height));
    backends.forEach((b, i) => pin(l[`m:${b.id}`], moons.current[i], 0.42, camera, size.width, size.height));
  });

  return (
    <>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[stars, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.12} color={COL.white} transparent opacity={0.7} depthWrite={false} />
      </points>
      <primitive object={orbit} />
      {/* the gateway core */}
      <mesh ref={core}>
        <sphereGeometry args={[0.85, 48, 32]} />
        <meshBasicMaterial color={COL.cyanHi} toneMapped={false} />
      </mesh>
      {[1.4, 1.9].map((r, i) => (
        <mesh key={r} rotation={[Math.PI / 2 + (i ? 0.35 : -0.2), 0, 0]}>
          <torusGeometry args={[r, 0.015, 8, 96]} />
          <meshBasicMaterial color={i ? COL.violet : COL.cyan} transparent opacity={0.6} toneMapped={false} />
        </mesh>
      ))}
      {/* phase planets */}
      {JOURNEY.map((j, i) => (
        <group key={j.group}>
          <mesh ref={(m) => { planets.current[i] = m; }}>
            <sphereGeometry args={[0.42, 32, 24]} />
            <meshStandardMaterial color={COL.planet} emissive={j.group === "Memory" ? COL.violet : COL.cyan} emissiveIntensity={0.55} roughness={0.35} metalness={0.2} />
          </mesh>
          <mesh ref={(m) => { halos.current[i] = m; }}>
            <sphereGeometry args={[0.62, 24, 16]} />
            <meshBasicMaterial color={j.group === "Memory" ? COL.violet : COL.cyan} transparent opacity={0.18} depthWrite={false} blending={THREE.AdditiveBlending} />
          </mesh>
        </group>
      ))}
      {/* backend moons */}
      {backends.map((b, i) => (
        <mesh key={b.id} ref={(m) => { moons.current[i] = m; }}>
          <sphereGeometry args={[0.2, 24, 16]} />
          <meshStandardMaterial color={COL.planet} emissive={COL.cyan} emissiveIntensity={1} />
        </mesh>
      ))}
      {/* comets */}
      <points>
        <bufferGeometry ref={trailGeo}>
          <bufferAttribute attach="attributes-position" args={[trailPos, 3]} />
          <bufferAttribute attach="attributes-color" args={[trailCol, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.32} vertexColors transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
    </>
  );
});

export default function Galaxy3D({ nodes, still }: { nodes: ArchNode[]; still?: boolean }) {
  const flash = useRef<Record<string, number>>({});
  const labels = useRef<Record<string, HTMLSpanElement | null>>({});
  const live = useRef(nodes);
  live.current = nodes;
  const key = nodes.filter((n) => n.kind === "backend").map((n) => `${n.id}|${n.label}`).join(",");
  const backends = useMemo(() => key.split(",").filter(Boolean).map((k) => ({ id: k.split("|")[0], label: k.split("|")[1] })), [key]);
  const label = (key: string) => (el: HTMLSpanElement | null) => { labels.current[key] = el; };
  return (
    <>
    <Canvas dpr={[1, 1.75]} frameloop={still ? "demand" : "always"} camera={{ position: [0, 3.6, 7.4], fov: 55 }} gl={{ antialias: false, alpha: false }}>
      <color attach="background" args={["#000000"]} />
      <ambientLight intensity={0.5} />
      <pointLight position={[0, 0, 0]} intensity={30} distance={14} color={COL.cyanHi} />
      <Scene backends={backends} live={live} flash={flash} labels={labels} />
      <OrbitControls enablePan={false} minDistance={5} maxDistance={16} autoRotate={!still} autoRotateSpeed={0.35} enableDamping />
      <EffectComposer multisampling={0}>
        <Bloom intensity={1.3} luminanceThreshold={0.15} luminanceSmoothing={0.3} mipmapBlur radius={0.7} />
      </EffectComposer>
    </Canvas>
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <span ref={label("core")} className="galaxy-label galaxy-core galaxy-pin">AEGIS gateway</span>
      {JOURNEY.map((j, i) => (
        <span key={j.group} ref={label(`p${i}`)} className="galaxy-label galaxy-pin" title={j.explains}><b>{j.title}</b></span>
      ))}
      {backends.map((b) => (
        <span key={b.id} ref={label(`m:${b.id}`)} className="galaxy-label galaxy-moon galaxy-pin">{b.label}</span>
      ))}
    </div>
    </>
  );
}
