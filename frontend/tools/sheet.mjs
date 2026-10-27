// Screenshot every page at the given widths into var/shots/final-<page>-<width>.png and report
// page errors and horizontal overflow.   node tools/sheet.mjs 1440,1280,768
import { chromium } from "@playwright/test";

const widths = (process.argv[2] ?? "1440,1280,768").split(",").map(Number);
const pages = ["/", "/architecture", "/control-room", "/chaos", "/playground", "/traces", "/docs"];
const browser = await chromium.launch();
for (const w of widths) {
  const page = await browser.newPage({ viewport: { width: w, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const overflow = [];
  for (const path of pages) {
    await page.goto(`http://127.0.0.1:5173${path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1800);
    await page.screenshot({ path: `../var/shots/final-${path.slice(1) || "home"}-${w}.png` });
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) overflow.push(path);
  }
  console.log(w, errors.length ? errors : "no page errors", overflow.length ? `HORIZONTAL OVERFLOW on ${overflow}` : "no horizontal overflow");
  await page.close();
}
await browser.close();
