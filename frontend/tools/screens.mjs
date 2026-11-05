// Captures each page of the running site as a picture for the tunnel and the curved reel.
import { chromium } from "@playwright/test";
const pages = [["architecture", "/architecture"], ["control-room", "/control-room"], ["chaos", "/chaos"], ["playground", "/playground"], ["traces", "/traces"], ["docs", "/docs"], ["home", "/"], ["assistant", "/docs"]];
const b = await chromium.launch({ headless: false, args: ["--use-angle=metal"] });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.addInitScript(() => localStorage.setItem("prahari-motion-paused", "0"));
for (const [name, path] of pages) {
  await p.goto(`http://127.0.0.1:5173${path}`, { waitUntil: "networkidle" });
  await p.waitForTimeout(2500);
  if (name === "assistant") { await p.keyboard.press("/"); await p.waitForTimeout(800); }
  if (name === "traces") { const first = p.locator('ul[aria-label="Traces"] button').first(); if (await first.count()) { await first.click(); await p.waitForTimeout(600); } }
  await p.screenshot({ path: `../var/screens/${name}.png` });
}
await b.close();
