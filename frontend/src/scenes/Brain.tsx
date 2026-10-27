// "Inside the brain": a three.js neural interior in five layered regions, one per gateway
// subsystem. Scroll progress (a motion value) drives the camera through them; each region runs a
// small vignette of what that subsystem does. Layout is seeded and decorative; the numbers in the
// captions come from the live gateway.
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import type { MotionValue } from "motion/react";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { dotTexture } from "./sprite";
import { mulberry32 } from "../lib/rng";

export const REGION_Z = [-8, -24, -40, -56, -72];
const REGION_COLOR = ["#74d7f7", "#c4a2f5", "#79e8b3", "#ffc25c", "#b8ecff"];
const NODES_PER_REGION = 70;

interface Graph {
  positions: Float32Array;
  colors: Float32Array;
  home: Float32Array;   // brain layout
  flat: Float32Array;   // constellation layout for the exit
  lines: Float32Array;
  lineColors: Float32Array;
  edges: [number, number][];
}

function buildGraph(): Graph {
  const rnd = mulberry32(42);
  const n = NODES_PER_REGION * REGION_Z.length;
  const home = new Float32Array(n * 3);
  const flat = new Float32Array(n * 3);
  const colors = new Float32Array(n * 3);
  for (let r = 0; r < REGION_Z.length; r++) {
    const col = new THREE.Color(REGION_COLOR[r]);
    for (let k = 0; k < NODES_PER_REGION; k++) {
      const i = r * NODES_PER_REGION + k;
      const a = rnd() * Math.PI * 2;
      const rad = 1.2 + rnd() * 4.2;
      home[i * 3] = Math.cos(a) * rad * 1.5;
      home[i * 3 + 1] = Math.sin(a) * rad * 0.8;
      home[i * 3 + 2] = REGION_Z[r] + (rnd() - 0.5) * 9;
      // exit: each region becomes one column of the architecture constellation
      flat[i * 3] = (r - 2) * 4.2 + (rnd() - 0.5) * 1.6;
      flat[i * 3 + 1] = (k / NODES_PER_REGION - 0.5) * 7 + (rnd() - 0.5) * 0.3;
      flat[i * 3 + 2] = -84;
      colors.set([col.r, col.g, col.b], i * 3);
    }
  }
  const edges: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    let best = -1;
    let second = -1;
    let bd = Infinity;
    let sd = Infinity;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const dx = home[i * 3] - home[j * 3];
      const dy = home[i * 3 + 1] - home[j * 3 + 1];
      const dz = home[i * 3 + 2] - home[j * 3 + 2];
      const d = dx * dx + dy * dy + dz * dz;
      if (d < bd) { second = best; sd = bd; best = j; bd = d; } else if (d < sd) { second = j; sd = d; }
    }
    if (best > i) edges.push([i, best]);
    if (second > i && sd < 14) edges.push([i, second]);
  }
  const lines = new Float32Array(edges.length * 6);
  const lineColors = new Float32Array(edges.length * 6);
  edges.forEach(([a, b], e) => {
    lines.set([home[a * 3], home[a * 3 + 1], home[a * 3 + 2], home[b * 3], home[b * 3 + 1], home[b * 3 + 2]], e * 6);
    lineColors.set([colors[a * 3], colors[a * 3 + 1], colors[a * 3 + 2], colors[b * 3], colors[b * 3 + 1], colors[b * 3 + 2]], e * 6);
  });
  return { positions: home.slice(), colors, home, flat, lines, lineColors, edges };
}

const PULSES = 140;

function Network({ progress }: { progress: MotionValue<number> }) {
  const g = useMemo(() => buildGraph(), []);
  const points = useRef<THREE.Points>(null);
  const lines = useRef<THREE.LineSegments>(null);
  const pulses = useRef<THREE.Points>(null);
  const pulseState = useMemo(() => {
    const rnd = mulberry32(9);
    return Array.from({ length: PULSES }, () => ({ edge: Math.floor(rnd() * g.edges.length), t: rnd(), speed: 0.25 + rnd() * 0.6 }));
  }, [g]);
  const pulsePos = useMemo(() => new Float32Array(PULSES * 3), []);
  const rnd = useMemo(() => mulberry32(3), []);

  useFrame((state, dt) => {
    const p = progress.get();
    // camera: 0.18 -> 0.84 flies through the regions; then pulls back for the constellation
    const fly = THREE.MathUtils.clamp((p - 0.16) / 0.68, 0, 1);
    const exit = THREE.MathUtils.clamp((p - 0.84) / 0.14, 0, 1);
    const cam = state.camera;
    cam.position.z = THREE.MathUtils.lerp(4, REGION_Z[4] + 6, fly) + exit * -2;
    cam.position.x = Math.sin(fly * Math.PI * 2) * 1.2 * (1 - exit);
    cam.position.y = Math.cos(fly * Math.PI * 3) * 0.5 * (1 - exit);
    cam.lookAt(0, 0, cam.position.z - 10);

    // exit morph: nodes ease from the brain into the constellation columns
    const k = exit * exit * (3 - 2 * exit);
    const pos = g.positions;
    for (let i = 0; i < pos.length; i++) pos[i] = g.home[i] + (g.flat[i] - g.home[i]) * k;
    if (points.current) points.current.geometry.attributes.position.needsUpdate = true;
    if (lines.current) {
      const lp = lines.current.geometry.attributes.position.array as Float32Array;
      g.edges.forEach(([a, b], e) => {
        lp[e * 6] = pos[a * 3]; lp[e * 6 + 1] = pos[a * 3 + 1]; lp[e * 6 + 2] = pos[a * 3 + 2];
        lp[e * 6 + 3] = pos[b * 3]; lp[e * 6 + 4] = pos[b * 3 + 1]; lp[e * 6 + 5] = pos[b * 3 + 2];
      });
      lines.current.geometry.attributes.position.needsUpdate = true;
      (lines.current.material as THREE.LineBasicMaterial).opacity = 0.22 - 0.12 * k;
    }

    // pulses travel along synapses
    for (let i = 0; i < PULSES; i++) {
      const s = pulseState[i];
      s.t += dt * s.speed;
      if (s.t > 1) {
        s.t = 0;
        s.edge = Math.floor(rnd() * g.edges.length);
      }
      const [a, b] = g.edges[s.edge];
      for (let c = 0; c < 3; c++) pulsePos[i * 3 + c] = pos[a * 3 + c] + (pos[b * 3 + c] - pos[a * 3 + c]) * s.t;
    }
    if (pulses.current) pulses.current.geometry.attributes.position.needsUpdate = true;
  });

  return (
    <group>
      <lineSegments ref={lines}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[g.lines, 3]} />
          <bufferAttribute attach="attributes-color" args={[g.lineColors, 3]} />
        </bufferGeometry>
        <lineBasicMaterial vertexColors transparent opacity={0.22} depthWrite={false} blending={THREE.AdditiveBlending} />
      </lineSegments>
      <points ref={points}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[g.positions, 3]} />
          <bufferAttribute attach="attributes-color" args={[g.colors, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.3} vertexColors transparent opacity={0.95} depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <points ref={pulses}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[pulsePos, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.36} color="#b8ecff" transparent opacity={0.95} depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <Vignettes progress={progress} />
    </group>
  );
}

// One small story per region, built from instanced points.
function Vignettes({ progress }: { progress: MotionValue<number> }) {
  const gate = useRef<THREE.Points>(null);
  const memory = useRef<THREE.Points>(null);
  const memoryHit = useRef<THREE.Mesh>(null);
  const hive = useRef<THREE.Points>(null);
  const reflexA = useRef<THREE.Line>(null);
  const reflexB = useRef<THREE.Line>(null);
  const reflexDot = useRef<THREE.Mesh>(null);
  const ledger = useRef<THREE.Points>(null);
  const clock = useRef(0);

  const gateN = 60;
  const gatePos = useMemo(() => new Float32Array(gateN * 3), []);
  const gateCol = useMemo(() => {
    const c = new Float32Array(gateN * 3);
    for (let i = 0; i < gateN; i++) {
      const pii = i % 7 === 0;
      c.set(pii ? [1, 0.42, 0.48] : [0.45, 0.84, 0.97], i * 3);
    }
    return c;
  }, []);
  const hivePos = useMemo(() => new Float32Array(10 * 3), []);
  const ledgerPos = useMemo(() => new Float32Array(40 * 3), []);
  const memPos = useMemo(() => new Float32Array(3), []);
  const pathA = useMemo(() => new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-3, 0.6, -56), new THREE.Vector3(0, 1.4, -56), new THREE.Vector3(3, 0.6, -56)]), []);
  const pathB = useMemo(() => new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-3, 0.6, -56), new THREE.Vector3(0, -1.3, -56), new THREE.Vector3(3, 0.6, -56)]), []);
  const lineA = useMemo(() => new THREE.Line(pathA, new THREE.LineBasicMaterial({ color: "#74d7f7", transparent: true })), [pathA]);
  const lineB = useMemo(() => new THREE.Line(pathB, new THREE.LineBasicMaterial({ color: "#ffc25c", transparent: true })), [pathB]);

  useFrame((_, dt) => {
    clock.current += dt;
    const t = clock.current;
    const visible = progress.get() > 0.12 && progress.get() < 0.9;
    if (!visible) return;
    // Gate: a stream flows right; PII shards (red) drop out of it at the gate line x=0.
    for (let i = 0; i < gateN; i++) {
      const u = ((t * 0.35 + i / gateN) % 1) * 2 - 1;
      const pii = i % 7 === 0;
      const x = u * 4;
      const y = (i % 5) * 0.18 - 0.36 - (pii && x > 0 ? x * x * 0.7 : 0);
      gatePos.set([x, y, REGION_Z[0] - 1], i * 3);
    }
    if (gate.current) gate.current.geometry.attributes.position.needsUpdate = true;
    // Memory: a question particle flies to its stored twin, which flashes violet.
    const m = (t * 0.4) % 1;
    memPos.set([-3.2 + m * 5.2, -0.6 + Math.sin(m * Math.PI) * 1.2, REGION_Z[1]], 0);
    if (memory.current) memory.current.geometry.attributes.position.needsUpdate = true;
    if (memoryHit.current) memoryHit.current.scale.setScalar(m > 0.92 ? 2.2 : 1);
    // Hive: ten identical particles converge into one, then fan back out.
    const h = (Math.sin(t * 1.1) + 1) / 2;
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2;
      hivePos.set([Math.cos(a) * 2.6 * h, Math.sin(a) * 1.6 * h, REGION_Z[2]], i * 3);
    }
    if (hive.current) hive.current.geometry.attributes.position.needsUpdate = true;
    // Reflexes: the top path goes dark every few seconds; traffic takes the lower path.
    const broken = Math.floor(t / 2.5) % 2 === 1;
    if (reflexA.current) (reflexA.current.material as THREE.LineBasicMaterial).color.set(broken ? "#ff6b7a" : "#74d7f7");
    if (reflexA.current) (reflexA.current.material as THREE.LineBasicMaterial).opacity = broken ? 0.25 : 0.9;
    if (reflexB.current) (reflexB.current.material as THREE.LineBasicMaterial).opacity = broken ? 0.9 : 0.25;
    if (reflexDot.current) {
      const s = (t * 0.8) % 1;
      const y = broken ? -1.3 * Math.sin(s * Math.PI) + 0.6 * (1 - Math.sin(s * Math.PI)) : 1.4 * Math.sin(s * Math.PI) + 0.6 * (1 - Math.sin(s * Math.PI));
      reflexDot.current.position.set(-3 + s * 6, y, REGION_Z[3]);
    }
    // Ledger: tokens rise into the ledger and settle.
    for (let i = 0; i < 40; i++) {
      const s = (t * 0.3 + i / 40) % 1;
      ledgerPos.set([((i % 8) - 3.5) * 0.7, -2 + s * 4, REGION_Z[4] - (i % 3)], i * 3);
    }
    if (ledger.current) ledger.current.geometry.attributes.position.needsUpdate = true;
  });

  return (
    <group>
      <points ref={gate}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[gatePos, 3]} />
          <bufferAttribute attach="attributes-color" args={[gateCol, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.2} vertexColors transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <mesh position={[0, 0, REGION_Z[0] - 1]}>
        <planeGeometry args={[0.04, 2.4]} />
        <meshBasicMaterial color="#74d7f7" transparent opacity={0.6} />
      </mesh>
      <points ref={memory}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[memPos, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.42} color="#eaf0ff" transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <mesh ref={memoryHit} position={[2, -0.6, REGION_Z[1]]}>
        <sphereGeometry args={[0.18, 16, 16]} />
        <meshBasicMaterial color="#c4a2f5" />
      </mesh>
      <points ref={hive}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[hivePos, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.32} color="#79e8b3" transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
      <primitive object={lineA} ref={reflexA} />
      <primitive object={lineB} ref={reflexB} />
      <mesh ref={reflexDot}>
        <sphereGeometry args={[0.14, 12, 12]} />
        <meshBasicMaterial color="#eaf0ff" />
      </mesh>
      <points ref={ledger}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[ledgerPos, 3]} />
        </bufferGeometry>
        <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.18} color="#b8ecff" transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
    </group>
  );
}

export default function Brain({ progress, active }: { progress: MotionValue<number>; active: boolean }) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      frameloop={active ? "always" : "never"}
      camera={{ position: [0, 0, 4], fov: 62, near: 0.1, far: 120 }}
      gl={{ antialias: false, alpha: true, powerPreference: "high-performance" }}
      aria-hidden="true"
    >
      <fog attach="fog" args={["#04050c", 6, 34]} />
      <Network progress={progress} />
      <EffectComposer multisampling={0}>
        <Bloom intensity={1.35} luminanceThreshold={0.08} luminanceSmoothing={0.35} mipmapBlur radius={0.75} />
        <Vignette eskil={false} offset={0.25} darkness={0.75} />
      </EffectComposer>
    </Canvas>
  );
}
