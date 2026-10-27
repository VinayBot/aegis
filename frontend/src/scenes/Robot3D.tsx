// AEGIS, the assistant: an original 3D robot modelled in three.js. A dark armoured sentinel that
// hovers: a graphite head with a black glass visor and one cyan scanner eye, armoured shoulders
// with lit trim, a glowing core in the chest, two detached floating hands, a cone thruster in
// place of legs streaming light onto a glowing disk, and a dashed ring in orbit round it all.
//   sleep:  the eye is dim and sweeps slowly; everything idles
//   awake:  the eye brightens and tracks the pointer; the parts lift slightly apart
//   boot:   the eye flashes, the ring spins up, the parts burst out and snap back together
import { Environment, Lightformer, RoundedBox } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { mulberry32 } from "../lib/rng";
import { dotTexture } from "./sprite";

export type RobotMood = "sleep" | "awake" | "boot";

// WebGL cannot read CSS tokens; restated from tokens.css.
const ARMOR = new THREE.Color("#e9edf5");
const ARMOR_HI = new THREE.Color("#f7f9fc");
const GLASS = new THREE.Color("#04060c");
const CYAN = new THREE.Color("#74d7f7");
const CYAN_HI = new THREE.Color("#b8ecff");
const VIOLET = new THREE.Color("#c4a2f5");
const SILVER = new THREE.Color("#c9d3ea");

const BEAM = 70;
const RING_DOTS = 54;

function Sentinel({ mood, pointer }: { mood: RobotMood; pointer: React.RefObject<[number, number]> }) {
  const root = useRef<THREE.Group>(null);
  const head = useRef<THREE.Group>(null);
  const eye = useRef<THREE.Mesh>(null);
  const eyeMat = useRef<THREE.MeshBasicMaterial>(null);
  const core = useRef<THREE.Mesh>(null);
  const coreMat = useRef<THREE.MeshBasicMaterial>(null);
  const handL = useRef<THREE.Group>(null);
  const handR = useRef<THREE.Group>(null);
  const torso = useRef<THREE.Group>(null);
  const cone = useRef<THREE.Group>(null);
  const ring = useRef<THREE.Group>(null);
  const beam = useRef<THREE.Points>(null);
  const disk = useRef<THREE.MeshBasicMaterial>(null);
  const spread = useRef(0);
  const boost = useRef(0);
  const blink = useRef({ next: 2.5, until: 0 });

  const rnd = useMemo(() => mulberry32(5), []);
  const beamParts = useMemo(() => Array.from({ length: BEAM }, () => ({ y: -rnd() * 1.1, a: rnd() * Math.PI * 2, r: rnd() * 0.12, v: 0.6 + rnd() })), [rnd]);
  const beamPos = useMemo(() => new Float32Array(BEAM * 3), []);
  const ringPos = useMemo(() => {
    const a = new Float32Array(RING_DOTS * 3);
    for (let i = 0; i < RING_DOTS; i++) {
      const t = (i / RING_DOTS) * Math.PI * 2;
      a.set([Math.cos(t) * 1.55, 0, Math.sin(t) * 1.55], i * 3);
    }
    return a;
  }, []);

  useFrame((state, dt) => {
    const t = state.clock.elapsedTime;
    const k = (rate: number) => Math.min(1, dt * rate);
    // how far apart the parts float, and how hard everything burns
    const spreadTarget = mood === "boot" ? 0.55 : mood === "awake" ? 0.14 : 0;
    spread.current += (spreadTarget - spread.current) * k(mood === "boot" ? 9 : 4);
    boost.current += ((mood === "boot" ? 1.6 : mood === "awake" ? 1 : 0.45) - boost.current) * k(4);
    const s = spread.current;

    if (root.current) root.current.position.y = Math.sin(t * 1.4) * 0.06;

    if (head.current) {
      head.current.position.y = 0.72 + s * 0.6 + Math.sin(t * 1.4 + 0.6) * 0.02;
      const [px, py] = pointer.current ?? [0, 0];
      const yaw = mood === "sleep" ? Math.sin(t * 0.45) * 0.18 : THREE.MathUtils.clamp(px, -1, 1) * 0.6;
      const pitch = mood === "sleep" ? 0.08 : THREE.MathUtils.clamp(py, -1, 1) * 0.25;
      head.current.rotation.y += (yaw - head.current.rotation.y) * k(5);
      head.current.rotation.x += (pitch - head.current.rotation.x) * k(5);
    }

    // the scanner eye: sweeps while asleep, tracks the pointer while awake, blinks now and then
    if (eye.current) {
      const [px] = pointer.current ?? [0, 0];
      const x = mood === "sleep" ? Math.sin(t * 0.9) * 0.22 : THREE.MathUtils.clamp(px, -1, 1) * 0.22;
      eye.current.position.x += (x - eye.current.position.x) * k(8);
      const b = blink.current;
      if (mood !== "sleep" && t > b.next) {
        b.until = t + 0.12;
        b.next = t + 2.6 + ((t * 13) % 2.4);
      }
      const open = t < b.until ? 0.1 : mood === "sleep" ? 0.55 : 1;
      eye.current.scale.y += (open - eye.current.scale.y) * k(20);
    }
    if (eyeMat.current) eyeMat.current.color.copy(CYAN_HI).multiplyScalar(mood === "boot" ? 2.2 : mood === "awake" ? 1.25 : 0.8);
    if (coreMat.current) coreMat.current.color.copy(CYAN).multiplyScalar(1.4 + Math.sin(t * 3) * 0.6 + boost.current);
    if (core.current) core.current.scale.setScalar(1 + Math.sin(t * 3) * 0.06);

    if (torso.current) torso.current.position.y = Math.sin(t * 1.4 + 0.3) * 0.015;
    if (handL.current) handL.current.position.set(-0.86 - s * 0.6, -0.02 + Math.sin(t * 1.9) * 0.07, 0.05);
    if (handR.current) handR.current.position.set(0.86 + s * 0.6, -0.02 + Math.sin(t * 1.9 + 1.7) * 0.07, 0.05);
    if (cone.current) {
      cone.current.position.y = -0.78 - s * 0.5 + Math.sin(t * 1.4 + 1) * 0.03;
      cone.current.rotation.y += dt * (0.6 + boost.current);
    }
    if (ring.current) {
      ring.current.rotation.y += dt * (0.5 + boost.current * 1.6);
      ring.current.rotation.z = 0.32 + Math.sin(t * 0.7) * 0.05;
      ring.current.scale.setScalar(1 + s * 0.35);
    }
    if (disk.current) disk.current.opacity = 0.35 + boost.current * 0.25 + Math.sin(t * 2.2) * 0.08;

    // the thruster's light stream
    beamParts.forEach((p, i) => {
      p.y -= dt * p.v * (0.5 + boost.current);
      if (p.y < -0.8) {
        p.y = 0;
        p.a = rnd() * Math.PI * 2;
        p.r = rnd() * 0.1;
      }
      const w = p.r * (1 + -p.y * 0.8);
      beamPos.set([Math.cos(p.a) * w, p.y, Math.sin(p.a) * w], i * 3);
    });
    if (beam.current) beam.current.geometry.attributes.position.needsUpdate = true;
  });

  const armor = <meshPhysicalMaterial color={ARMOR} metalness={0.08} roughness={0.3} clearcoat={1} clearcoatRoughness={0.08} envMapIntensity={1.1} />;
  const armorHi = <meshPhysicalMaterial color={ARMOR_HI} metalness={0.08} roughness={0.26} clearcoat={1} envMapIntensity={1.1} />;

  return (
    <group ref={root}>
      {/* the orbiting dashed ring */}
      <group ref={ring} position={[0, 0.05, 0]}>
        <points>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[ringPos, 3]} />
          </bufferGeometry>
          <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.09} color={CYAN} transparent opacity={0.9} depthWrite={false} blending={THREE.AdditiveBlending} />
        </points>
      </group>

      {/* head */}
      <group ref={head} position={[0, 0.72, 0]}>
        <RoundedBox args={[1.2, 0.78, 0.92]} radius={0.32} smoothness={6}>{armor}</RoundedBox>
        {/* visor band */}
        <RoundedBox args={[1.08, 0.38, 0.2]} radius={0.12} smoothness={5} position={[0, 0.02, 0.4]}>
          <meshPhysicalMaterial color={GLASS} metalness={0.3} roughness={0.04} clearcoat={1} clearcoatRoughness={0.01} envMapIntensity={2.4} />
        </RoundedBox>
        {/* the scanner eye */}
        <mesh ref={eye} position={[-0.18, 0.03, 0.512]}>
          <planeGeometry args={[0.34, 0.2]} />
          <meshBasicMaterial ref={eyeMat} color={CYAN_HI} toneMapped={false} />
        </mesh>
        {/* antenna fin */}
        <mesh position={[0, 0.5, -0.05]} rotation={[0, Math.PI / 4, 0]}>
          <coneGeometry args={[0.14, 0.26, 4]} />
          <meshPhysicalMaterial color={SILVER} metalness={0.9} roughness={0.2} envMapIntensity={2} />
        </mesh>
        {/* side lights */}
        {[-1, 1].map((x) => (
          <mesh key={x} position={[x * 0.61, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
            <torusGeometry args={[0.13, 0.025, 10, 28]} />
            <meshBasicMaterial color={CYAN} toneMapped={false} />
          </mesh>
        ))}
      </group>

      {/* torso with shoulders and the core */}
      <group ref={torso}>
        <RoundedBox args={[0.92, 0.66, 0.72]} radius={0.22} smoothness={5} position={[0, -0.12, 0]}>{armor}</RoundedBox>
        {[-1, 1].map((x) => (
          <group key={x} position={[x * 0.56, 0.12, 0]}>
            <RoundedBox args={[0.42, 0.26, 0.66]} radius={0.12} smoothness={4}>{armorHi}</RoundedBox>
            <mesh position={[0, -0.02, 0.335]}>
              <planeGeometry args={[0.32, 0.05]} />
              <meshBasicMaterial color={VIOLET} toneMapped={false} />
            </mesh>
          </group>
        ))}
        <mesh position={[0, -0.12, 0.37]}>
          <torusGeometry args={[0.14, 0.02, 10, 32]} />
          <meshBasicMaterial color={CYAN} toneMapped={false} />
        </mesh>
        <mesh ref={core} position={[0, -0.12, 0.34]}>
          <sphereGeometry args={[0.09, 24, 16]} />
          <meshBasicMaterial ref={coreMat} color={CYAN} toneMapped={false} />
        </mesh>
      </group>

      {/* detached floating hands */}
      {[handL, handR].map((ref, i) => (
        <group key={i} ref={ref}>
          <RoundedBox args={[0.24, 0.38, 0.28]} radius={0.1} smoothness={4}>{armorHi}</RoundedBox>
          <mesh position={[0, 0.2, 0]} rotation={[Math.PI / 2, 0, 0]}>
            <torusGeometry args={[0.1, 0.018, 8, 24]} />
            <meshBasicMaterial color={CYAN} toneMapped={false} />
          </mesh>
        </group>
      ))}

      {/* cone thruster and its light stream */}
      <group ref={cone} position={[0, -0.78, 0]}>
        <mesh rotation={[Math.PI, 0, 0]}>
          <coneGeometry args={[0.24, 0.42, 4]} />
          <meshPhysicalMaterial color={ARMOR_HI} metalness={0.08} roughness={0.3} clearcoat={1} />
        </mesh>
        <mesh position={[0, -0.22, 0]}>
          <sphereGeometry args={[0.05, 16, 12]} />
          <meshBasicMaterial color={CYAN_HI} toneMapped={false} />
        </mesh>
        <points ref={beam} position={[0, -0.24, 0]}>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[beamPos, 3]} />
          </bufferGeometry>
          <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.07} color={CYAN} transparent opacity={0.85} depthWrite={false} blending={THREE.AdditiveBlending} />
        </points>
      </group>

      {/* the glowing disk it hovers over */}
      <mesh position={[0, -1.8, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.75, 48]} />
        <meshBasicMaterial ref={disk} color={CYAN} transparent opacity={0.4} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
    </group>
  );
}

export default function Robot3D({ mood, pointer, onReady, still }: {
  mood: RobotMood; flying?: boolean; pointer: React.RefObject<[number, number]>; onReady?: () => void; still?: boolean;
}) {
  return (
    <Canvas
      dpr={[1, 2]}
      frameloop={still ? "demand" : "always"}
      camera={{ position: [0, 0.1, 7.4], fov: 30 }}
      gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
      onCreated={() => onReady?.()}
      aria-hidden="true"
      style={{ pointerEvents: "none" }}
    >
      {/* a tiny local studio for reflections on the armour and glass: no HDR download */}
      <Environment resolution={64} frames={1}>
        <Lightformer form="rect" intensity={3} position={[0, 4, 4]} scale={[8, 3, 1]} />
        <Lightformer form="rect" intensity={1.2} position={[-5, 0, 2]} scale={[3, 6, 1]} />
        <Lightformer form="ring" intensity={4} color={CYAN} position={[4, 0, -1]} scale={2.5} />
      </Environment>
      <ambientLight intensity={0.25} />
      <directionalLight position={[2, 3, 4]} intensity={1.6} />
      <pointLight position={[0, -1.2, 1.4]} intensity={3} distance={4} color={CYAN} />
      <Sentinel mood={mood} pointer={pointer} />
    </Canvas>
  );
}
