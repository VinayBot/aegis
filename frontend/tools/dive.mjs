// Captures the scroll dive at a few points (Mac GPU).   node tools/dive.mjs
import { chromium } from "@playwright/test";
const b = await chromium.launch({ headless: false, args: ["--use-angle=metal"] });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await p.waitForTimeout(800);
const top = await p.evaluate(() => document.querySelector('section[aria-labelledby="dive-title"]').offsetTop);
const H = await p.evaluate(() => document.querySelector('section[aria-labelledby="dive-title"]').offsetHeight - window.innerHeight);
for (const f of [0.26, 0.55, 0.96]) {
  await p.evaluate((y) => window.scrollTo(0, y), Math.round(top + H * f));
  await p.waitForTimeout(2400);
  await p.screenshot({ path: `../var/shots/neuron-${Math.round(f * 100)}.png` });
}
await b.close();
