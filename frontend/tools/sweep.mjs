// Loads every page at 1440, 1280, 768 and 390 px and reports console errors, failed requests,
// horizontal overflow, heading count, images without alt and unnamed controls; saves phone shots.
//   node tools/sweep.mjs [out-dir]   (SITE=http://127.0.0.1:8000 to sweep the production build)
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";
const OUT = process.argv[2] ?? "../var/sweep";
mkdirSync(OUT, { recursive: true });
const SITE = process.env.SITE ?? "http://127.0.0.1:5173";
const pages = ["/", "/architecture", "/architecture?view=detailed", "/control-room", "/chaos", "/playground", "/traces", "/docs", "/docs/ARCHITECTURE.md", "/no-such-page"];
const b = await chromium.launch({ args: ["--use-angle=metal"] });
const ignore = /GL Driver Message|THREE\.Clock|React Router Future|Download the React DevTools/;
const links = new Set();
for (const [w, h] of [[1440, 900], [1280, 800], [768, 1024], [390, 844]]) {
  for (const path of pages) {
    const ctx = await b.newContext({ viewport: { width: w, height: h } });
    const p = await ctx.newPage();
    const problems = [];
    p.on("console", (m) => { if ((m.type() === "error" || m.type() === "warning") && !ignore.test(m.text())) problems.push(`${m.type()}: ${m.text().slice(0, 160)}`); });
    p.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 160)}`));
    p.on("response", (r) => { if (r.status() >= 400 && !r.url().includes("favicon")) problems.push(`HTTP ${r.status()} ${r.url().replace(SITE, "")}`); });
    p.on("requestfailed", (r) => { if (!/\/ws\/|hot-update/.test(r.url())) problems.push(`failed ${r.url().replace(SITE, "")} ${r.failure()?.errorText}`); });
    await p.goto(SITE + path, { waitUntil: "networkidle" }).catch((e) => problems.push("goto " + e.message));
    await p.waitForTimeout(1500);
    // scroll the whole page to trigger lazy parts
    const total = await p.evaluate(() => document.documentElement.scrollHeight);
    for (let y = 0; y < total; y += h * 0.8) { await p.evaluate((yy) => window.scrollTo(0, yy), y); await p.waitForTimeout(120); }
    await p.waitForTimeout(800);
    const info = await p.evaluate(() => {
      const over = document.documentElement.scrollWidth - window.innerWidth;
      const wide = over > 1 ? [...document.querySelectorAll("body *")].filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== "fixed").slice(0, 3).map((e) => e.tagName + "." + String(e.className).slice(0, 50)) : [];
      const h1 = document.querySelectorAll("h1").length;
      const imgsNoAlt = [...document.images].filter((i) => !i.hasAttribute("alt")).length;
      const broken = [...document.images].filter((i) => i.complete && i.naturalWidth === 0 && i.src).map((i) => i.src.slice(-50));
      const unnamed = [...document.querySelectorAll("button, a[href]")].filter((e) => !(e.getAttribute("aria-label") || e.textContent.trim() || e.getAttribute("title") || e.querySelector("img[alt]"))).length;
      const hrefs = [...document.querySelectorAll("a[href^='/']")].map((a) => a.getAttribute("href"));
      return { over, wide, h1, imgsNoAlt, broken, unnamed, hrefs };
    });
    info.hrefs.forEach((x) => links.add(x));
    const flags = [];
    if (info.over > 1) flags.push(`horizontal overflow ${info.over}px ${info.wide.join(" | ")}`);
    if (info.h1 !== 1) flags.push(`h1 count ${info.h1}`);
    if (info.imgsNoAlt) flags.push(`${info.imgsNoAlt} img without alt`);
    if (info.broken.length) flags.push(`broken images ${info.broken.join(",")}`);
    if (info.unnamed) flags.push(`${info.unnamed} unnamed buttons/links`);
    console.log(`${w} ${path.padEnd(28)} ${[...new Set(problems)].concat(flags).join("\n      ") || "ok"}`);
    if (w <= 768) await p.screenshot({ path: `${OUT}/${w}${path.replace(/[/?=.]/g, "_")}.png` });
    await ctx.close();
  }
}
console.log("internal links:", [...links].sort().join(" "));
await b.close();
