"""What a serving engine says about itself: vLLM's own Prometheus metrics, read by the gateway.

A gateway that only counts its own slots sees vLLM as a black box. vLLM knows better: how many
requests it is running, how many are queued behind them, how full its paged KV cache is, and how
often its prefix cache hits. The health monitor reads `/metrics` beside each probe, and the router
uses it: a backend whose engine reports a queue (`saturation_waiting`) or a nearly full KV cache
(`saturation_kv`) goes to the back of the chain for new requests, so traffic spills to the next
backend before latency climbs, not after. It is never dropped: when it is the only backend left,
it still serves.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

STALE_AFTER_S = 5.0  # older stats say nothing about now


@dataclass(frozen=True)
class EngineStats:
    running: float
    waiting: float
    kv_cache_usage: float | None  # 0..1
    prefix_cache_hit_rate: float | None  # 0..1, since the engine started
    at: float

    def fresh(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.at < STALE_AFTER_S

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "waiting": self.waiting,
            "kv_cache_usage": self.kv_cache_usage,
            "prefix_cache_hit_rate": self.prefix_cache_hit_rate,
        }


def _sums(text: str) -> dict[str, float]:
    """Sum each `vllm:` metric over its label sets (one engine or several)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith("vllm:"):
            continue
        name_labels, _, value = line.rpartition(" ")
        name = name_labels.split("{", 1)[0]
        try:
            out[name] = out.get(name, 0.0) + float(value)
        except ValueError:
            continue
    return out


def parse_vllm(text: str, at: float | None = None) -> EngineStats | None:
    m = _sums(text)
    if "vllm:num_requests_running" not in m:
        return None
    kv = m.get("vllm:kv_cache_usage_perc", m.get("vllm:gpu_cache_usage_perc"))
    queries = m.get("vllm:prefix_cache_queries_total")
    hits = m.get("vllm:prefix_cache_hits_total")
    hit_rate = hits / queries if queries and hits is not None else None
    return EngineStats(
        running=m.get("vllm:num_requests_running", 0.0),
        waiting=m.get("vllm:num_requests_waiting", 0.0),
        kv_cache_usage=kv,
        prefix_cache_hit_rate=round(hit_rate, 4) if hit_rate is not None else None,
        at=at if at is not None else time.time(),
    )


def metrics_url(base_url: str) -> str:
    """vLLM serves /metrics at the server root, beside /v1."""
    root = base_url.rstrip("/")
    return (root[: -len("/v1")] if root.endswith("/v1") else root) + "/metrics"


async def scrape(client: httpx.AsyncClient, base_url: str) -> EngineStats | None:
    try:
        resp = await client.get(metrics_url(base_url), timeout=1.0)
        resp.raise_for_status()
    except Exception:
        return None
    return parse_vllm(resp.text)
