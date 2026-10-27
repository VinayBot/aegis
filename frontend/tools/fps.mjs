// Frame-time profile in a real Chromium on the Mac GPU (Metal). Usage: node fps.mjs
import { chromium } from "@playwright/test";
const browser = await chromium.launch({ headless: false, args: ["--use-angle=metal", "--ignore-gpu-blocklist", "--window-position=0,0"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
async function measure(label, action, ms = 4000) {
  await page.evaluate(() => {
    window.__ft = []; let last = performance.now();
    const tick = (t) => { window.__ft.push(t - last); last = t; window.__raf = requestAnimationFrame(tick); };
    window.__raf = requestAnimationFrame(tick);
  });
  await action();
  await page.waitForTimeout(ms);
  const ft = await page.evaluate(() => { cancelAnimationFrame(window.__raf); return window.__ft.slice(2); });
  ft.sort((a, b) => a - b);
  const avg = ft.reduce((a, b) => a + b, 0) / ft.length;
  const p95 = ft[Math.floor(ft.length * 0.95)];
  const jank = ft.filter((x) => x > 25).length;
  console.log(`${label.padEnd(34)} fps ${(1000 / avg).toFixed(0).padStart(3)}  p95 frame ${p95.toFixed(1)} ms  janky frames ${jank}/${ft.length}`);
}
await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" }); await page.waitForTimeout(2500);
await measure("home: hero idle", async () => {});
await measure("home: scroll through dive", async () => { for (let i = 0; i < 40; i++) { await page.mouse.wheel(0, 120); await page.waitForTimeout(80); } }, 1500);
await measure("home: meet stage + bot", async () => { await page.evaluate(() => document.querySelector("#meet-title")?.scrollIntoView()); });
await page.goto("http://127.0.0.1:5173/architecture?view=detailed", { waitUntil: "networkidle" }); await page.waitForTimeout(2000);
await measure("architecture: burst traffic", async () => { fetch("http://127.0.0.1:8000/api/playground/burst", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ n: 6 }) }); });
await page.goto("http://127.0.0.1:5173/architecture", { waitUntil: "networkidle" }); await page.waitForTimeout(3000);
await measure("architecture: 3D galaxy + traffic", async () => { fetch("http://127.0.0.1:8000/api/playground/burst", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ n: 6 }) }); });
await page.goto("http://127.0.0.1:5173/control-room", { waitUntil: "networkidle" }); await page.waitForTimeout(2000);
await measure("control room: idle", async () => {});
await browser.close();
