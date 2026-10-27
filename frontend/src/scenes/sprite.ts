import * as THREE from "three";

let cached: THREE.Texture | null = null;

/** A soft round glow used by every point cloud, so particles read as light, not squares. */
export function dotTexture(): THREE.Texture {
  if (cached) return cached;
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.85)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  cached = new THREE.CanvasTexture(canvas);
  return cached;
}
