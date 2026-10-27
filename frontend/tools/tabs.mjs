// Time from clicking a nav tab until the new page and its heading are fully shown, and any blank time.
//   node tools/tabs.mjs   (SITE=http://127.0.0.1:8000 for the production build)
import { chromium } from "@playwright/test";
const SITE = process.env.SITE ?? "http://127.0.0.1:5173";
const b = await chromium.launch({ args: ["--use-angle=metal"] }); const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
await p.goto(SITE + "/", { waitUntil: "networkidle" }); await p.waitForTimeout(3000);
const tabs = [["Architecture","/architecture"],["Control Room","/control-room"],["Chaos Lab","/chaos"],["Playground","/playground"],["Traces","/traces"],["Docs","/docs"],["Architecture","/architecture"]];
for (const [name, path] of tabs) {
  const ms = await p.evaluate(([name, path]) => new Promise((resolve) => {
    const link = [...document.querySelectorAll('nav[aria-label="Main"] a')].find((a) => a.textContent.trim() === name);
    const oldMain = document.querySelector("main"); const t0 = performance.now(); let blank = 0; let last = t0;
    link.click();
    const tick = () => {
      const main = document.querySelector("main"); const h1 = document.querySelector("h1");
      const op = main ? Number(getComputedStyle(main).opacity) : 0;
      const hop = h1 ? Math.min(...[h1, ...h1.querySelectorAll("*")].map((e) => Number(getComputedStyle(e).opacity))) : 0;
      const now = performance.now();
      if (!main || !main.textContent.trim() || op < 0.3) blank += now - last;
      last = now;
      if (location.pathname === path && main !== oldMain && h1 && main.contains(h1) && op > 0.95 && hop > 0.95) resolve({ settled: Math.round(now - t0), blank: Math.round(blank) });
      else if (now - t0 > 8000) resolve({ settled: -1, blank: Math.round(blank) });
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }), [name, path]);
  console.log(`${name.padEnd(13)} settled in ${ms.settled} ms, blank for ${ms.blank} ms`);
  await p.waitForTimeout(700);
}
await b.close();
