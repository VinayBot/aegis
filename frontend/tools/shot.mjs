// Usage: node shot.mjs <path> <width> <out> [scrollY] [waitMs]
import { chromium } from "@playwright/test";
const [, , path = "/", width = "1440", out = "../../../var/shots/shot.png", scrollY = "0", wait = "1500"] = process.argv;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: Number(width), height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.goto(`http://127.0.0.1:5173${path}`, { waitUntil: "networkidle" });
if (Number(scrollY)) { await page.mouse.wheel(0, Number(scrollY)); }
await page.waitForTimeout(Number(wait));
await page.screenshot({ path: out });
console.log(out, errors.length ? "ERRORS:\n" + errors.slice(0, 8).join("\n") : "no console errors");
await browser.close();
