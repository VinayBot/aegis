"""Load test against the running gateway (`make dev`): `make loadtest`.

Fires a realistic mix at a fixed concurrency and reports throughput, latency and time-to-first-
token percentiles, the cache-hit share and every error. Two phases:

  overhead  cache hits only: what the gateway itself costs per request (auth, guardrails, limits,
            Redis lookup, SSE), with no model in the way
  mixed     a workload where some prompts repeat (the realistic case for a cache): model calls,
            exact and semantic hits and coalescing all happen at once

    uv run python ../scripts/loadtest.py --requests 120 --concurrency 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter
from typing import Any

import httpx

TOPICS = [
    "a circuit breaker",
    "semantic caching",
    "request coalescing",
    "hedged requests",
    "a token bucket rate limiter",
    "server-sent events",
    "OpenTelemetry tracing",
    "continuous batching in vLLM",
]


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


async def one(
    client: httpx.AsyncClient, url: str, key: str, prompt: str, no_cache: bool
) -> dict[str, Any]:
    body = {
        "model": "prahari-chat",
        "stream": True,
        "temperature": 0,
        "max_tokens": 48,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"authorization": f"Bearer {key}"}
    if no_cache:
        headers["cache-control"] = "no-cache"
    start = time.perf_counter()
    ttft = None
    try:
        async with client.stream(
            "POST", f"{url}/v1/chat/completions", json=body, headers=headers
        ) as resp:
            async for line in resp.aiter_lines():
                if ttft is None and line.startswith("data: {") and '"content"' in line:
                    ttft = time.perf_counter() - start
            return {
                "status": resp.status_code,
                "cache": resp.headers.get("x-cache", "?"),
                "backend": resp.headers.get("x-prahari-backend", "?"),
                "latency": time.perf_counter() - start,
                "ttft": ttft,
            }
    except Exception as exc:
        return {"status": 0, "cache": "error", "backend": type(exc).__name__, "latency": 0, "ttft": None}


async def phase(
    name: str, url: str, key: str, prompts: list[str], concurrency: int, no_cache: bool = False
) -> dict[str, Any]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=120) as client:

        async def run(p: str) -> dict[str, Any]:
            async with sem:
                return await one(client, url, key, p, no_cache)

        start = time.perf_counter()
        results = await asyncio.gather(*(run(p) for p in prompts))
        wall = time.perf_counter() - start
    ok = [r for r in results if r["status"] == 200]
    lat = [r["latency"] * 1000 for r in ok]
    ttft = [r["ttft"] * 1000 for r in ok if r["ttft"] is not None]
    caches = Counter(r["cache"] for r in results)
    return {
        "phase": name,
        "requests": len(results),
        "concurrency": concurrency,
        "wall_s": round(wall, 2),
        "throughput_rps": round(len(results) / wall, 2),
        "errors": len(results) - len(ok),
        "status": dict(Counter(r["status"] for r in results)),
        "cache": dict(caches),
        "backends": dict(Counter(r["backend"] for r in ok)),
        "latency_ms": {
            "p50": round(pct(lat, 50), 1),
            "p95": round(pct(lat, 95), 1),
            "p99": round(pct(lat, 99), 1),
            "mean": round(statistics.fmean(lat), 1) if lat else None,
        },
        "ttft_ms": {"p50": round(pct(ttft, 50), 1), "p95": round(pct(ttft, 95), 1)},
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default="prahari-demo-key")
    ap.add_argument("--requests", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    run_id = int(time.time())

    # overhead: warm one prompt, then hit it repeatedly (every request is an exact cache hit)
    warm = f"In one sentence, what is {TOPICS[0]}? (load {run_id})"
    await phase("warm", args.url, args.key, [warm], 1)
    await asyncio.sleep(1.0)  # the cache write happens after the safety check, off the request path
    overhead = await phase("overhead (cache hits)", args.url, args.key, [warm] * 60, args.concurrency)

    # mixed: 8 topics x a few phrasings, drawn with repeats, so caches and coalescing all engage
    pool = []
    for t in TOPICS:
        pool += [
            f"In one sentence, what is {t}? (mix {run_id})",
            f"in one sentence, what is {t} (mix {run_id})",
        ]
    prompts = [rng.choice(pool) for _ in range(args.requests)]
    mixed = await phase("mixed workload", args.url, args.key, prompts, args.concurrency)

    report = {"run_id": run_id, "phases": [overhead, mixed]}
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
