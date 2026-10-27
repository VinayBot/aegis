import { chromium } from "@playwright/test";
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" }); await p.waitForTimeout(1200);
await (await p.$("#meet-title")).scrollIntoViewIfNeeded(); await p.mouse.move(900, 200); await p.waitForTimeout(3200);
await p.screenshot({ path: "../var/shots/meet2.png" }); await b.close();
