// "Inside the brain": real neuron anatomy in three.js, under bloom. Each neuron has a glowing
// cell body (soma), branching curved dendrites, and one long axon that ends in synaptic boutons
// on another neuron. Action potentials travel down the axons and flash the cell they reach.
//
// The camera flies through five regions, one per gateway subsystem, and each region fires the
// way its subsystem works:
//   Gate      some signals are red and die halfway (blocked: PII, injection)
//   Memory    violet signals, and the target cell lights up in recall
//   Hive      every axon converges on one hub neuron (many requests, one upstream call)
//   Reflexes  amber signals (hedges and failovers)
//   Ledger    steady, rhythmic firing (every request recorded)
// Layout and timing are seeded and decorative; the numbers on the captions are live.
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import type { MotionValue } from "motion/react";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { mulberry32 } from "../lib/rng";
import { dotTexture } from "./sprite";

export const REGION_Z = [-8, -24, -40, -56, -72];
const PER_REGION = 13;
const PULSES = 150;
const FLASHES = 40;

// WebGL cannot read CSS tokens, so the scene's palette is restated here from tokens.css.
const C = {
  dendrite: new THREE.Color("#1f5f86"),
  dendriteTip: new THREE.Color("#0b1b2c"),
  axon: new THREE.Color("#3f9fd0"),
  soma: new THREE.Color("#c9f1ff"),
  cyan: new THREE.Color("#8fe6ff"),
  violet: new THREE.Color("#c9a8ff"),
  amber: new THREE.Color("#ffc25c"),
  red: new THREE.Color("#ff5a6e"),
};
const REGION_PULSE = [C.cyan, C.violet, C.cyan, C.amber, C.cyan];

interface Neuron { pos: THREE.Vector3; region: number }
interface Axon { from: number; to: number; points: THREE.Vector3[]; region: number }

function grow(rnd: () => number) {
  const neurons: Neuron[] = [];
  for (let r = 0; r < REGION_Z.length; r++) {
    for (let k = 0; k < PER_REGION; k++) {
      const a = rnd() * Math.PI * 2;
      const rad = 1.4 + rnd() * 4.6;
      neurons.push({ pos: new THREE.Vector3(Math.cos(a) * rad * 1.35, Math.sin(a) * rad * 0.75, REGION_Z[r] + (rnd() - 0.5) * 11), region: r });
    }
  }

  // Dendrites: 5-7 curved branches per soma, each forking once or twice, fading to the tips.
  const dSeg: number[] = [];
  const dCol: number[] = [];
  const fade = (t: number) => C.dendrite.clone().lerp(C.dendriteTip, Math.min(1, t));
  const branch = (start: THREE.Vector3, dir: THREE.Vector3, length: number, depth: number, t0: number) => {
    const steps = 7 + Math.floor(rnd() * 5);
    let p = start.clone();
    const d = dir.clone().normalize();
    for (let i = 0; i < steps; i++) {
      d.add(new THREE.Vector3((rnd() - 0.5) * 0.7, (rnd() - 0.5) * 0.7, (rnd() - 0.5) * 0.7)).normalize();
      const q = p.clone().addScaledVector(d, length / steps);
      const ta = t0 + (i / steps) * (1 - t0);
      const tb = t0 + ((i + 1) / steps) * (1 - t0);
      dSeg.push(p.x, p.y, p.z, q.x, q.y, q.z);
      const a = fade(ta);
      const b = fade(tb);
      dCol.push(a.r, a.g, a.b, b.r, b.g, b.b);
      if (depth < 2 && i > 2 && rnd() < 0.22) branch(q, d.clone().add(new THREE.Vector3(rnd() - 0.5, rnd() - 0.5, rnd() - 0.5)), length * 0.55, depth + 1, tb);
      p = q;
    }
  };
  for (const n of neurons) {
    const count = 5 + Math.floor(rnd() * 3);
    for (let b = 0; b < count; b++) branch(n.pos, new THREE.Vector3(rnd() - 0.5, rnd() - 0.5, (rnd() - 0.5) * 0.8), 0.9 + rnd() * 1.1, 0, 0);
  }

  // Axons: one per neuron, an arcing curve to another neuron. In the Hive every axon converges on
  // one hub; elsewhere it reaches a near neighbour, sometimes in the next region.
  const axons: Axon[] = [];
  const hub = 2 * PER_REGION;
  neurons.forEach((n, i) => {
    let to = -1;
    if (n.region === 2 && i !== hub) to = hub;
    else {
      let best = Infinity;
      neurons.forEach((m, j) => {
        if (j === i) return;
        const d = n.pos.distanceTo(m.pos) + (m.region < n.region ? 40 : 0) + rnd() * 2.5;
        if (d > 1.8 && d < best) {
          best = d;
          to = j;
        }
      });
    }
    if (to < 0) return;
    const a = n.pos;
    const b = neurons[to].pos;
    const mid = a.clone().lerp(b, 0.5).add(new THREE.Vector3((rnd() - 0.5) * 2.2, (rnd() - 0.5) * 1.8 + 0.6, (rnd() - 0.5) * 1.5));
    const mid2 = a.clone().lerp(b, 0.8).add(new THREE.Vector3((rnd() - 0.5) * 1, (rnd() - 0.5) * 1, 0));
    axons.push({ from: i, to, points: new THREE.CatmullRomCurve3([a, mid, mid2, b]).getPoints(48), region: n.region });
  });
  const aSeg: number[] = [];
  const aCol: number[] = [];
  const boutons: number[] = [];
  for (const ax of axons) {
    for (let k = 0; k < ax.points.length - 1; k++) {
      const p = ax.points[k];
      const q = ax.points[k + 1];
      aSeg.push(p.x, p.y, p.z, q.x, q.y, q.z);
      const col = C.axon.clone().multiplyScalar(0.55 + 0.45 * Math.sin((k / ax.points.length) * Math.PI));
      aCol.push(col.r, col.g, col.b, col.r, col.g, col.b);
    }
    const end = ax.points[ax.points.length - 1];
    for (let s = 0; s < 4; s++) boutons.push(end.x + (rnd() - 0.5) * 0.35, end.y + (rnd() - 0.5) * 0.35, end.z + (rnd() - 0.5) * 0.35);
  }
  const somas = new Float32Array(neurons.length * 3);
  neurons.forEach((n, i) => somas.set([n.pos.x, n.pos.y, n.pos.z], i * 3));
  return {
    neurons, axons, somas,
    dendrites: new Float32Array(dSeg), dendriteColors: new Float32Array(dCol),
    axonLines: new Float32Array(aSeg), axonColors: new Float32Array(aCol),
    boutons: new Float32Array(boutons),
  };
}

interface Pulse { axon: number; t: number; speed: number; blocked: boolean; red: boolean; wait: number }
interface Flash { pos: THREE.Vector3; t: number; color: THREE.Color }

function Cortex({ progress }: { progress: MotionValue<number> }) {
  const anatomy = useMemo(() => grow(mulberry32(42)), []);
  const rnd = useMemo(() => mulberry32(7), []);
  const pulseGeo = useRef<THREE.BufferGeometry>(null);
  const flashGeo = useRef<THREE.BufferGeometry>(null);
  const somaMat = useRef<THREE.PointsMaterial>(null);

  const pulses = useMemo<Pulse[]>(() => Array.from({ length: PULSES }, () => ({ axon: 0, t: 0, speed: 0, blocked: false, red: false, wait: rnd() * 3 })), [rnd]);
  const pulsePos = useMemo(() => new Float32Array(PULSES * 3 * 3).fill(-999), []);
  const pulseCol = useMemo(() => new Float32Array(PULSES * 3 * 3), []);
  const flashes = useMemo<Flash[]>(() => Array.from({ length: FLASHES }, () => ({ pos: new THREE.Vector3(0, -999, 0), t: 9, color: C.cyan })), []);
  const flashPos = useMemo(() => new Float32Array(FLASHES * 3), []);
  const flashCol = useMemo(() => new Float32Array(FLASHES * 3), []);
  const nextFlash = useRef(0);

  useFrame((state, dt) => {
    const prog = progress.get();
    const fly = THREE.MathUtils.clamp((prog - 0.16) / 0.7, 0, 1);
    const exit = THREE.MathUtils.clamp((prog - 0.86) / 0.12, 0, 1);
    const cam = state.camera;
    // fly down the cortex, then rise and pull back to see the whole network at once
    cam.position.z = THREE.MathUtils.lerp(4, REGION_Z[4] + 6, fly) + exit * 34;
    cam.position.x = Math.sin(fly * Math.PI * 2) * 1.3 * (1 - exit) + exit * 12;
    cam.position.y = Math.cos(fly * Math.PI * 3) * 0.6 * (1 - exit) + exit * 9;
    cam.lookAt(0, 0, THREE.MathUtils.lerp(cam.position.z - 10, -40, exit));
    if (somaMat.current) somaMat.current.size = 0.42 + Math.sin(state.clock.elapsedTime * 1.7) * 0.05;

    for (let i = 0; i < PULSES; i++) {
      const p = pulses[i];
      if (p.wait > 0) {
        p.wait -= dt;
        if (p.wait <= 0) {
          p.axon = Math.floor(rnd() * anatomy.axons.length);
          const region = anatomy.axons[p.axon].region;
          p.t = 0;
          p.speed = region === 4 ? 0.55 : 0.35 + rnd() * 0.45;
          p.red = region === 0 && rnd() < 0.3;
          p.blocked = p.red;
        } else {
          for (let k = 0; k < 3; k++) pulsePos.set([0, -999, 0], (i * 3 + k) * 3);
          continue;
        }
      }
      p.t += dt * p.speed;
      const ax = anatomy.axons[p.axon];
      if (p.t >= (p.blocked ? 0.5 : 1)) {
        if (!p.blocked) {
          const f = flashes[nextFlash.current++ % FLASHES];
          f.pos.copy(anatomy.neurons[ax.to].pos);
          f.t = 0;
          f.color = REGION_PULSE[ax.region];
        }
        p.wait = 0.2 + rnd() * 1.8;
        continue;
      }
      const color = p.red ? C.red : REGION_PULSE[ax.region];
      const dim = p.blocked ? 1 - Math.max(0, (p.t - 0.35) / 0.15) : 1;
      for (let k = 0; k < 3; k++) {
        const tt = Math.max(0, p.t - k * 0.025);
        const pt = ax.points[Math.min(ax.points.length - 1, Math.floor(tt * (ax.points.length - 1)))];
        pulsePos.set([pt.x, pt.y, pt.z], (i * 3 + k) * 3);
        const s = dim * (1 - k * 0.3);
        pulseCol.set([color.r * s, color.g * s, color.b * s], (i * 3 + k) * 3);
      }
    }
    if (pulseGeo.current) {
      pulseGeo.current.attributes.position.needsUpdate = true;
      pulseGeo.current.attributes.color.needsUpdate = true;
    }

    for (let i = 0; i < FLASHES; i++) {
      const f = flashes[i];
      f.t += dt;
      const s = Math.max(0, 1 - f.t / 0.7);
      flashPos.set([f.pos.x, f.pos.y, f.pos.z], i * 3);
      flashCol.set([f.color.r * s, f.color.g * s, f.color.b * s], i * 3);
    }
    if (flashGeo.current) {
      flashGeo.current.attributes.position.needsUpdate = true;
      flashGeo.current.attributes.color.needsUpdate = true;
    }
  });

  return (
    <group>
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[anatomy.dendrites, 3]} />
          <bufferAttribute attach="attributes-color" args={[anatomy.dendriteColors, 3]} />
        </bufferGeometry>
        <lineBasicMaterial vertexColors transparent opacity={0.9} depthWrite={false} blending={THREE.AdditiveBlending} />
      </lineSegments>
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[anatomy.axonLines, 3]} />
          <bufferAttribute attach="attributes-color" args={[anatomy.axonColors, 3]} />
        </bufferGeometry>
        <lineBasicMaterial vertexColors transparent opacity={0.55} depthWrite={false} blending={THREE.AdditiveBlending} />
      </lineSegments>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[anatomy.somas, 3]} />
        </bufferGeometry>
        <pointsMaterial ref={somaMat} map={dotTexture()} alphaTest={0.01} size={0.42} color={C.soma} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[anatomy.boutons, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.09} color={C.cyan} transparent opacity={0.8} depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <points>
        <bufferGeometry ref={pulseGeo}>
          <bufferAttribute attach="attributes-position" args={[pulsePos, 3]} />
          <bufferAttribute attach="attributes-color" args={[pulseCol, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.26} vertexColors transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <points>
        <bufferGeometry ref={flashGeo}>
          <bufferAttribute attach="attributes-position" args={[flashPos, 3]} />
          <bufferAttribute attach="attributes-color" args={[flashCol, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={1.3} vertexColors transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
    </group>
  );
}

export default function Brain({ progress, active }: { progress: MotionValue<number>; active: boolean }) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      frameloop={active ? "always" : "never"}
      camera={{ position: [0, 0, 4], fov: 62, near: 0.1, far: 140 }}
      gl={{ antialias: false, alpha: false, powerPreference: "high-performance" }}
      aria-hidden="true"
    >
      {/* Opaque deep space: faded dendrite tips must fade into black, not punch holes in the page. */}
      <color attach="background" args={["#000000"]} />
      <fog attach="fog" args={["#000000", 5, 30]} />
      <Cortex progress={progress} />
      <EffectComposer multisampling={0}>
        <Bloom intensity={1.6} luminanceThreshold={0.05} luminanceSmoothing={0.4} mipmapBlur radius={0.8} />
        <Vignette eskil={false} offset={0.25} darkness={0.8} />
      </EffectComposer>
    </Canvas>
  );
}
