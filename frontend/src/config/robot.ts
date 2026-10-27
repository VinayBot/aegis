// Every overlay region on the robot pictures, in the exported image's own pixel space.
// Measured by scripts/build_assets.py and written to robotRegions.json; adjust there.
import regions from "./robotRegions.json";

export type Pt = [number, number];

export const HOVER = regions.hover as {
  width: number;
  height: number;
  screen: Pt[];
  eyes: { left: Box; right: Box };
  mouth: Box;
  tablet: Pt[]; // TL, TR, BR, BL of the live-chart area
};
export const STANDING = regions.standing as {
  scene: { width: number; height: number; eyes: { left: Ring; right: Ring }; visor: string };
  cut: { width: number; height: number; eyes: { left: Ring; right: Ring } };
  bot: { width: number; height: number };
};

export interface Box { cx: number; cy: number; w: number; h: number }
export interface Ring { cx: number; cy: number; r: number }

export const polygon = (pts: Pt[]) => pts.map(([x, y]) => `${x},${y}`).join(" ");

/** Affine map of the unit square onto the tablet quad (TL, TR, BL); its 4th corner is within a
 *  few pixels of the measured one, so an SVG matrix() is enough. */
export function quadMatrix(q: Pt[]): string {
  const [tl, tr, , bl] = q;
  const a = tr[0] - tl[0];
  const b = tr[1] - tl[1];
  const c = bl[0] - tl[0];
  const d = bl[1] - tl[1];
  return `matrix(${a / 100} ${b / 100} ${c / 100} ${d / 100} ${tl[0]} ${tl[1]})`;
}

export const ASSETS = {
  hover: { base: "/assets/robot-hover", widths: [480, 760, 1188] },
  standing: { base: "/assets/robot-standing", widths: [420, 690] },
  scene: { base: "/assets/robot-standing-scene", widths: [800, 1278] },
  orb: { base: "/assets/robot-orb", widths: [96, 192] },
  bot: { base: "/assets/robot-bot", widths: [160, 320] },
};

export const srcSet = (base: string, widths: number[], ext: "avif" | "webp") =>
  widths.map((w) => `${base}-${w}.${ext} ${w}w`).join(", ");
