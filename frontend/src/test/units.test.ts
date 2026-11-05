import { vi as viEarly } from "vitest";
viEarly.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { duration, PacketDriver, registry, type PacketKind } from "../components/arch/packets";
import { fmtMs, fmtPct, fmtUsd } from "../lib/format";
import { rephrase } from "../pages/Playground";
import type { GatewayEvent } from "../lib/types";

describe("formatting", () => {
  it("formats latency, percentages and est. dollars", () => {
    expect(fmtMs(7)).toBe("7 ms");
    expect(fmtMs(1274)).toBe("1.27 s");
    expect(fmtMs(null)).toBe("—");
    expect(fmtPct(0.115, 1)).toBe("11.5%");
    expect(fmtUsd(0)).toBe("$0");
    expect(fmtUsd(0.00004155)).toBe("$0.00004");
  });
});

describe("rephrase and resend", () => {
  it("is deterministic and actually changes the prompt", () => {
    const p = "What is the refund policy for annual plans?";
    expect(rephrase(p, 0)).toBe(rephrase(p, 0));
    for (let i = 0; i < 4; i++) expect(rephrase(p, i)).not.toBe(p);
  });
});

describe("packet engine", () => {
  let seq = 0;
  const ev = (type: string, extra: Record<string, unknown> = {}): GatewayEvent => ({ seq: ++seq, ts: 0, type, request_id: "r1", ...extra });

  function capture() {
    const launched: [string, PacketKind][] = [];
    const ids = ["client->auth", "auth->guardrails", "exact_cache->relay", "relay->client", "router->backend:mlx", "backend:mlx->relay", "router->hedger", "hedger->backend:ollama", "relay->ledger_queue"];
    const offs = ids.map((id) => registry.edge(id, { launch: (kind) => launched.push([id, kind]) }));
    return { launched, done: () => offs.forEach((f) => f()) };
  }

  it("draws a cache hit as violet and short-circuits to the relay", () => {
    const { launched, done } = capture();
    const d = new PacketDriver("detailed");
    for (const e of [ev("request.start"), ev("stage.enter", { stage: "auth" }), ev("stage.exit", { stage: "auth", ms: 1 }),
      ev("stage.enter", { stage: "guardrails" }), ev("cache.hit", { kind: "exact" }), ev("stage.enter", { stage: "relay" }),
      ev("request.end", { status: "ok", cache: "exact" })]) d.handle(e);
    expect(launched).toContainEqual(["client->auth", "normal"]);
    expect(launched).toContainEqual(["exact_cache->relay", "cached"]);
    expect(launched).toContainEqual(["relay->client", "cached"]);
    d.dispose();
    done();
  });

  it("draws a hedge as amber on both backends and the winner's relay", () => {
    const { launched, done } = capture();
    const d = new PacketDriver("detailed");
    for (const e of [ev("request.start"), ev("stage.enter", { stage: "router" }), ev("backend.try", { backend: "mlx" }),
      ev("hedge.fire", { primary: "mlx", hedge: "ollama" }), ev("backend.try", { backend: "ollama", hedge: true }),
      ev("hedge.win", { winner: "ollama", losers: ["mlx"] }), ev("backend.first_token", { backend: "ollama", ttft_ms: 100 })]) d.handle(e);
    expect(launched).toContainEqual(["router->hedger", "hedge"]);
    expect(launched).toContainEqual(["hedger->backend:ollama", "hedge"]);
    d.dispose();
    done();
  });

  it("does nothing for a request it never saw start (no invented paths)", () => {
    const { launched, done } = capture();
    const d = new PacketDriver("detailed");
    d.handle({ seq: 1, ts: 0, type: "stage.enter", request_id: "unknown", stage: "auth" });
    expect(launched).toHaveLength(0);
    d.dispose();
    done();
  });

  it("makes slow stages crawl", () => {
    expect(duration(2000)).toBeGreaterThan(duration(5));
    expect(duration(1e6)).toBeLessThanOrEqual(3200);
  });
});

describe("no invented data", () => {
  it("never uses Math.random anywhere in src", () => {
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(ts|tsx)$/.test(name) && !p.endsWith("units.test.ts") && readFileSync(p, "utf8").includes("Math.random")) hits.push(p);
      }
    };
    walk(join(__dirname, ".."));
    expect(hits).toEqual([]);
  });
});

vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));

import { repoUrl } from "../pages/Docs";

describe("doc links to source files", () => {
  it("resolve from the document's own folder to the file on GitHub", () => {
    expect(repoUrl("backend/prahari/router.py", "README.md")).toBe("https://github.com/VinayBot/aegis/blob/main/backend/prahari/router.py");
    expect(repoUrl("evidence/loadtest.json", "PRODUCTION_READINESS.md")).toBe("https://github.com/VinayBot/aegis/blob/main/docs/evidence/loadtest.json");
    expect(repoUrl("../Dockerfile", "ARCHITECTURE.md")).toBe("https://github.com/VinayBot/aegis/blob/main/Dockerfile");
  });
});
