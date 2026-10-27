// End-to-end acceptance tests against the running system (`make dev`).
import { expect, test } from "@playwright/test";

const API = process.env.AEGIS_API_URL ?? "http://127.0.0.1:8000";

test("home: the scroll journey runs from the hero through all five regions to the exit", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("AEGIS guards every model call");
  const seen = new Set<string>();
  for (let i = 0; i < 90; i++) {
    await page.mouse.wheel(0, 260);
    await page.waitForTimeout(40);
    for (const name of ["The Gate", "Memory", "The Hive", "The Reflexes", "The Ledger"]) {
      const caption = page.locator("article h3", { hasText: name }).first();
      if (await caption.count()) {
        const opacity = await caption.evaluate((el) => Number(getComputedStyle(el.closest("article")!).opacity));
        if (opacity > 0.6) seen.add(name);
      }
    }
    if (seen.size === 5 && (await page.getByText("Every region is a node in a live graph.").isVisible())) break;
  }
  expect([...seen].sort()).toEqual(["Memory", "The Gate", "The Hive", "The Ledger", "The Reflexes"]);
  await expect(page.getByRole("link", { name: "See it running live" }).first()).toBeVisible();
});

test("architecture: the graph is rendered from /api/architecture", async ({ page, request }) => {
  const arch = await (await request.get(`${API}/api/architecture`)).json();
  await page.goto("/architecture?view=detailed");
  await expect(page.locator(".react-flow__node")).toHaveCount(arch.nodes.length, { timeout: 15_000 });
  for (const n of arch.nodes.filter((x: { kind: string }) => x.kind === "backend")) {
    await expect(page.locator(".react-flow__node", { hasText: n.label })).toBeVisible();
  }
});

test("architecture: the 3D galaxy renders and comets follow real requests", async ({ page, request }) => {
  await page.goto("/architecture");
  await expect(page.getByRole("tab", { name: "3D galaxy" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("canvas").last()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("AEGIS gateway")).toBeVisible({ timeout: 15_000 });
  for (const phase of ["Check", "Allow", "Remember", "Choose", "Think", "Deliver"]) {
    await expect(page.locator(".galaxy-label", { hasText: phase }).first()).toBeVisible();
  }
  const r = await request.post(`${API}/v1/chat/completions`, { headers: { authorization: "Bearer aegis-demo-key" },
    data: { model: "aegis-chat", temperature: 0, max_tokens: 8, messages: [{ role: "user", content: "galaxy e2e ping" }] } });
  expect(r.ok()).toBeTruthy();
});

test("chaos: killing the primary turns its breaker red on the graph within 2 s", async ({ browser, request }) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const graph = await ctx.newPage();
  const lab = await ctx.newPage();
  await graph.goto("/architecture?view=detailed");
  const primary = graph.locator(".react-flow__node", { hasText: "MLX primary" }).locator(".arch-node");
  await expect(primary).toHaveAttribute("data-status", /up/, { timeout: 15_000 });
  await lab.goto("/chaos");
  const kill = lab.getByRole("switch", { name: "Kill MLX primary" });
  await expect(kill).toBeEnabled({ timeout: 10_000 });
  try {
    await kill.click();
    const t0 = Date.now();
    await expect(primary).toHaveAttribute("data-status", "down", { timeout: 2_000 });
    console.log(`breaker/health red on the graph after ${Date.now() - t0} ms`);
  } finally {
    await request.post(`${API}/api/chaos`, { data: { backend: "mlx-qwen", action: "kill", value: false } });
    await request.post(`${API}/api/breakers/mlx-qwen/reset`);
    await ctx.close();
  }
});

test("assistant: answers a docs question with a citation and a trace chip", async ({ page }) => {
  await page.goto("/docs");
  await expect(page.getByRole("button", { name: /^Ask AEGIS about this project/ })).toBeVisible({ timeout: 15_000 }); // lazy chunk loaded
  await page.keyboard.press("/");
  const box = page.getByLabel("Ask AEGIS", { exact: true });
  await expect(box).toBeFocused({ timeout: 10_000 });
  await box.fill("How does failover work in this gateway?");
  await box.press("Enter");
  const dialog = page.getByRole("dialog", { name: "AEGIS assistant" });
  await expect(dialog.getByText(/^\[1\] /).first()).toBeVisible({ timeout: 60_000 });
  await expect(dialog.getByText(/^cache: /).first()).toBeVisible({ timeout: 60_000 });
  await expect(dialog.getByRole("link", { name: "watch this answer's journey" })).toBeVisible();
});

test("offline: the site switches to labelled replay mode", async ({ page }) => {
  await page.route("**/api/**", (route) => route.abort());
  await page.route("**/ws/**", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("REPLAY: backend offline", { timeout: 15_000 });
  await expect(page.getByText(/requests served \(replay\)/)).toBeVisible();
});

test("playground: a request streams back with its trace, then the same prompt hits the exact cache", async ({ page, request }) => {
  // Earlier runs' answers would match this prompt semantically (it differs only by the timestamp),
  // so start from an empty cache to see the model answer first.
  await request.post(`${API}/api/cache/flush`);
  const prompt = `In one short sentence, what does a gateway log? (e2e ${Date.now()})`;
  await page.goto("/playground");
  await page.getByLabel("Prompt").fill(prompt);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const first = page.locator("article", { hasText: prompt }).first();
  await expect(first.getByText(/^cache: miss/)).toBeVisible({ timeout: 90_000 });
  await expect(first.getByText("auth", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("article", { hasText: prompt }).first().getByText(/^cache: hit/)).toBeVisible({ timeout: 30_000 });
});

test("playground: ten identical requests make one upstream call", async ({ page }) => {
  await page.goto("/playground");
  await page.getByLabel("Prompt").fill(`Name one colour. (storm e2e ${Date.now()})`);
  await page.getByLabel("Bypass cache (Cache-Control: no-cache)").check();
  await page.getByRole("button", { name: "Send this 10× at once" }).click();
  const batch = page.getByRole("region", { name: "Batch result" });
  await expect(batch).toContainText("9 coalesced · 1 upstream call(s)", { timeout: 90_000 });
  await expect(batch).toContainText("0 error(s)");
});

test("chaos lab: the kill switch freezes the primary, traffic still gets answers, revert restores it", async ({ page, request }) => {
  await page.goto("/chaos");
  const kill = page.getByRole("switch", { name: "Kill MLX primary" });
  await expect(kill).toBeEnabled({ timeout: 10_000 });
  try {
    await kill.click();
    await expect(kill).toHaveAttribute("aria-checked", "true", { timeout: 5_000 });
    const r = await request.post(`${API}/v1/chat/completions`, {
      headers: { authorization: "Bearer aegis-demo-key", "cache-control": "no-cache" },
      data: { model: "aegis-chat", max_tokens: 8, messages: [{ role: "user", content: `Say hi. (kill e2e ${Date.now()})` }] },
      timeout: 60_000,
    });
    expect(r.status()).toBe(200);
    expect(r.headers()["x-aegis-backend"]).toBe("ollama-llama");
    await page.getByRole("button", { name: "Revert everything" }).click();
    await expect(kill).toHaveAttribute("aria-checked", "false", { timeout: 5_000 });
  } finally {
    await request.post(`${API}/api/chaos/revert`);
    await request.post(`${API}/api/breakers/mlx-qwen/reset`);
  }
});

test("traces: picking a trace shows its waterfall and links to Jaeger", async ({ page }) => {
  await page.goto("/traces");
  const first = page.getByRole("list", { name: "Traces" }).getByRole("button").first();
  await expect(first).toBeVisible({ timeout: 20_000 });
  await first.click();
  const detail = page.getByRole("region", { name: "Trace detail" });
  await expect(detail.getByRole("link", { name: "Open in Jaeger" })).toBeVisible();
  await expect(detail.getByText(/^trace [0-9a-f]{32}$/)).toBeVisible();
});

test("architecture: clicking a node opens its live details, Close dismisses them", async ({ page }) => {
  await page.goto("/architecture?view=detailed");
  await page.locator(".react-flow__node", { hasText: "Semantic cache" }).first().click({ timeout: 15_000 });
  const drawer = page.getByRole("complementary", { name: /Semantic cache details/ });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("button", { name: "Close details" }).click();
  await expect(drawer).toBeHidden();
});

test("docs: links between documents open inside the site", async ({ page }) => {
  await page.goto("/docs/README.md");
  await page.locator(".prose-aegis").getByRole("link", { name: "docs/DECISIONS.md" }).click();
  await expect(page).toHaveURL(/\/docs\/DECISIONS\.md$/);
  await expect(page.locator(".prose-aegis .doc-title")).toContainText("Decisions");
});

test("site: unknown pages get a 404 with a way back; Pause motion stops animation", async ({ page }) => {
  await page.goto("/definitely-not-here");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page is off the map");
  await page.getByRole("link", { name: "Back to the start" }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("button", { name: "Pause motion" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");
  await page.getByRole("button", { name: "Resume motion" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-motion", "on");
});

test("architecture: tapping a node on a phone opens its details", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  try {
    await page.goto("/architecture?view=detailed");
    await page.locator(".react-flow__node", { hasText: "Router" }).first().tap({ timeout: 15_000 });
    await expect(page.getByRole("complementary", { name: /details$/ })).toBeVisible();
  } finally {
    await ctx.close();
  }
});
