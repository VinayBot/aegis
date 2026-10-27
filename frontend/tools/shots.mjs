// node shots.mjs <path> <width> <prefix> <ms,ms,...> [scrollY]
import { chromium } from "@playwright/test";
const [, , path, width, prefix, times, scrollY = "0"] = process.argv;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: Number(width), height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto(`http://127.0.0.1:5173${path}`, { waitUntil: "domcontentloaded" });
if (Number(scrollY)) await page.mouse.wheel(0, Number(scrollY));
let last = 0;
for (const t of times.split(",").map(Number)) {
  await page.waitForTimeout(t - last); last = t;
  await page.screenshot({ path: `${prefix}-${t}.png` });
}
console.log(errors.length ? errors.join("\n") : "no page errors");
await browser.close();
