"""Serving benchmark: why vLLM's continuous batching matters (`make bench-serving`).

Sends the same short generation to a model server at concurrency 1, 4 and 8, straight to the
server (no gateway in between), and reports aggregate output tokens per second and the latency
each request saw. A server that batches (vLLM: paged attention, continuous batching) should
grow its aggregate throughput with concurrency; one that serves one request at a time
(mlx_lm.server) keeps it flat while each request waits longer.

    uv run python ../scripts/bench_serving.py --out ../docs/evidence/serving_bench.json

Different model sizes (vLLM here runs a 0.5B model, MLX a 3B one), so compare each server's
scaling with itself, not the absolute numbers across servers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

import httpx

# name -> (base url, model, extra request fields). Every request must generate exactly
# max_tokens so throughput is comparable: vLLM is told to ignore end-of-sequence; the 3B model
# fills the budget on the counting task by itself.
SERVERS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "vllm-metal (Qwen2.5-0.5B)": (
        "http://127.0.0.1:8090/v1",
        "qwen2.5-0.5b",
        {"ignore_eos": True},
    ),
    "mlx_lm.server (Qwen2.5-3B)": (
        "http://127.0.0.1:8081/v1",
        "mlx-community/Qwen2.5-3B-Instruct-4bit",
        {},
    ),
}
# a task both models answer at length, so every request generates up to max_tokens
PROMPT = "Count from 1 to 200, separated by commas. Do not stop early."


async def one(
    client: httpx.AsyncClient, base: str, model: str, max_tokens: int, extra: dict[str, Any]
) -> tuple[float, int]:
    start = time.perf_counter()
    r = await client.post(
        f"{base}/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": max_tokens,
            "temperature": 0,
            **extra,
        },
    )
    r.raise_for_status()
    tokens = int(r.json().get("usage", {}).get("completion_tokens") or 0)
    return time.perf_counter() - start, tokens


async def level(
    base: str, model: str, extra: dict[str, Any], concurrency: int, max_tokens: int
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=300) as client:
        await one(client, base, model, 8, extra)  # warm
        start = time.perf_counter()
        results = await asyncio.gather(
            *(one(client, base, model, max_tokens, extra) for _ in range(concurrency))
        )
        wall = time.perf_counter() - start
    tokens = sum(t for _, t in results)
    latencies = [lat for lat, _ in results]
    return {
        "concurrency": concurrency,
        "output_tokens": tokens,
        "wall_s": round(wall, 2),
        "aggregate_tokens_per_s": round(tokens / wall, 1),
        "mean_latency_s": round(statistics.fmean(latencies), 2),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--max-tokens", type=int, default=96)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    report: dict[str, Any] = {"prompt": PROMPT, "max_tokens": args.max_tokens, "servers": {}}
    for name, (base, model, extra) in SERVERS.items():
        rows = []
        for c in (int(x) for x in args.levels.split(",")):
            rows.append(await level(base, model, extra, c, args.max_tokens))
        base_tps = rows[0]["aggregate_tokens_per_s"] or 1
        for r in rows:
            r["speedup_vs_1"] = round(r["aggregate_tokens_per_s"] / base_tps, 2)
        report["servers"][name] = rows
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
