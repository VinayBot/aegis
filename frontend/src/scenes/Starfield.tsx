// A slow-drifting starfield: one instanced Points draw call, under 3k stars, DPR capped at 2,
// rendering only while on screen. Decorative; positions come from a seeded generator.
import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { dotTexture } from "./sprite";
import { mulberry32 } from "../lib/rng";

function Stars({ count }: { count: number }) {
  const ref = useRef<THREE.Points>(null);
  const [positions, colors] = useMemo(() => {
    const rnd = mulberry32(7);
    const p = new Float32Array(count * 3);
    const c = new Float32Array(count * 3);
    const cyan = new THREE.Color("#74d7f7");
    const violet = new THREE.Color("#c4a2f5");
    const white = new THREE.Color("#eaf0ff");
    for (let i = 0; i < count; i++) {
      const r = 6 + rnd() * 22;
      const th = rnd() * Math.PI * 2;
      const ph = Math.acos(2 * rnd() - 1);
      p[i * 3] = r * Math.sin(ph) * Math.cos(th);
      p[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th) * 0.6;
      p[i * 3 + 2] = -Math.abs(r * Math.cos(ph));
      const pick = rnd();
      const col = pick < 0.12 ? cyan : pick < 0.22 ? violet : white;
      c.set([col.r, col.g, col.b], i * 3);
    }
    return [p, c];
  }, [count]);
  useFrame((_, dt) => {
    if (ref.current) {
      ref.current.rotation.y += dt * 0.006;
      ref.current.rotation.x += dt * 0.0015;
    }
  });
  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <pointsMaterial map={dotTexture()} alphaTest={0.01} size={0.038} vertexColors transparent opacity={0.7} sizeAttenuation depthWrite={false} />
    </points>
  );
}

export default function Starfield({ active = true, count = 1800 }: { active?: boolean; count?: number }) {
  return (
    <Canvas
      dpr={[1, 2]}
      frameloop={active ? "always" : "never"}
      camera={{ position: [0, 0, 4], fov: 60 }}
      gl={{ antialias: false, alpha: true, powerPreference: "low-power" }}
      aria-hidden="true"
    >
      <Stars count={count} />
    </Canvas>
  );
}
