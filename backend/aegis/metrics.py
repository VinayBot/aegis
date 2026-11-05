"""Live metrics, aggregated from the event bus into per-second buckets (last 15 minutes).

The bus is the single source of truth: anything the dashboard plots is something the gateway
published as an event.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from aegis.events import EventBus
from aegis.router import percentile

HORIZON_S = 900


@dataclass
class Bucket:
    requests: int = 0
    ok: int = 0
    cache_exact: int = 0
    cache_semantic: int = 0
    cache_miss: int = 0
    coalesced: int = 0
    errors: int = 0
    rejected: int = 0
    blocked: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    saved: float = 0.0
    cost: float = 0.0
    hedges_fired: int = 0
    hedges_won_by_hedge: int = 0
    failovers: int = 0
    ttft: list[float] = field(default_factory=list)
    latency: list[float] = field(default_factory=list)
    served_by: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tokens_by_key: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    stage_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))


class Metrics:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.buckets: dict[int, Bucket] = {}
        self.stage_latency: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=300))
        self.stage_counts: dict[str, int] = defaultdict(int)
        self.breaker_history: deque[dict[str, Any]] = deque(maxlen=500)
        self.recent_requests: deque[dict[str, Any]] = deque(maxlen=200)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._consume())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _consume(self) -> None:
        async with self.bus.subscribe() as queue:
            while True:
                self.observe(await queue.get())

    def _bucket(self, ts: float) -> Bucket:
        second = int(ts)
        bucket = self.buckets.get(second)
        if bucket is None:
            bucket = self.buckets[second] = Bucket()
            if len(self.buckets) > HORIZON_S + 60:
                cutoff = second - HORIZON_S
                for key in [k for k in self.buckets if k < cutoff]:
                    del self.buckets[key]
        return bucket

    def observe(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        ts = event["ts"]
        if kind == "stage.exit":
            stage = event["stage"]
            ms = float(event.get("ms", 0.0))
            self.stage_latency[stage].append(ms)
            self.stage_counts[stage] += 1
            self._bucket(ts).stage_ms[stage].append(ms)
        elif kind == "hedge.fire":
            self._bucket(ts).hedges_fired += 1
        elif kind == "failover":
            self._bucket(ts).failovers += 1
        elif kind == "breaker.state":
            self.breaker_history.append(
                {
                    "ts": ts,
                    "backend": event["backend"],
                    "state": event["state"],
                    "reason": event.get("reason"),
                }
            )
        elif kind == "request.end":
            self._observe_end(event)

    def _observe_end(self, e: dict[str, Any]) -> None:
        b = self._bucket(e["ts"])
        b.requests += 1
        status = e.get("status")
        if status == "ok":
            b.ok += 1
        elif status == "rejected":
            b.rejected += 1
        elif status == "blocked":
            b.blocked += 1
        else:
            b.errors += 1
        cache = e.get("cache")
        if cache == "exact":
            b.cache_exact += 1
        elif cache == "semantic":
            b.cache_semantic += 1
        elif status == "ok":
            b.cache_miss += 1
        if e.get("coalesced"):
            b.coalesced += 1
        b.tokens_in += int(e.get("tokens_in") or 0)
        b.tokens_out += int(e.get("tokens_out") or 0)
        b.tokens_by_key[e.get("key_id") or "?"] += int(e.get("tokens_in") or 0) + int(
            e.get("tokens_out") or 0
        )
        b.saved += float(e.get("saved_est_usd") or 0.0)
        b.cost += float(e.get("cost_est_usd") or 0.0)
        if e.get("hedge_won"):
            b.hedges_won_by_hedge += 1
        if (
            e.get("ttft_ms") is not None
            and cache not in ("exact", "semantic")
            and not e.get("coalesced")
        ):
            b.ttft.append(float(e["ttft_ms"]))
        if e.get("latency_ms") is not None:
            b.latency.append(float(e["latency_ms"]))
        if e.get("backend") and status == "ok" and cache not in ("exact", "semantic"):
            b.served_by[e["backend"]] += 1
        self.recent_requests.appendleft(
            {
                k: e.get(k)
                for k in (
                    "request_id",
                    "ts",
                    "tenant",
                    "model",
                    "status",
                    "http_status",
                    "cache",
                    "backend",
                    "coalesced",
                    "hedged",
                    "failovers",
                    "tokens_in",
                    "tokens_out",
                    "latency_ms",
                    "ttft_ms",
                    "saved_est_usd",
                    "cost_est_usd",
                    "trace_id",
                    "error",
                    "similarity",
                    "usage_estimated",
                    "redactions",
                )
            }
        )

    # ------------------------------------------------------------------ reads

    def stage_stats(self, stage: str) -> dict[str, Any]:
        values = list(self.stage_latency.get(stage, ()))
        return {
            "p50_ms": percentile(values, 0.5),
            "p95_ms": percentile(values, 0.95),
            "count": self.stage_counts.get(stage, 0),
        }

    def series(
        self, window_s: int = 300, step_s: int = 5, now: float | None = None
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        end = int(now)
        start = end - window_s + 1
        steps = math.ceil(window_s / step_s)
        out: dict[str, list[Any]] = defaultdict(list)
        backends: set[str] = set()
        stages = ("guardrails", "ratelimit", "exact_cache", "semantic_cache", "router", "relay")
        for i in range(steps):
            lo = start + i * step_s
            group = [self.buckets[s] for s in range(lo, lo + step_s) if s in self.buckets]
            req = sum(g.requests for g in group)
            exact = sum(g.cache_exact for g in group)
            sem = sum(g.cache_semantic for g in group)
            miss = sum(g.cache_miss for g in group)
            ttft = [v for g in group for v in g.ttft]
            lat = [v for g in group for v in g.latency]
            out["t"].append(lo + step_s)
            out["rps"].append(round(req / step_s, 3))
            out["requests"].append(req)
            out["cache_exact"].append(exact)
            out["cache_semantic"].append(sem)
            out["cache_miss"].append(miss)
            lookups = exact + sem + miss
            out["hit_rate"].append(round((exact + sem) / lookups, 4) if lookups else None)
            out["ttft_p50"].append(_r(percentile(ttft, 0.5)))
            out["ttft_p95"].append(_r(percentile(ttft, 0.95)))
            out["latency_p95"].append(_r(percentile(lat, 0.95)))
            out["tokens_per_min"].append(
                round(sum(g.tokens_in + g.tokens_out for g in group) * 60 / step_s)
            )
            out["saved"].append(round(sum(g.saved for g in group), 8))
            out["errors"].append(sum(g.errors for g in group))
            out["rejected"].append(sum(g.rejected for g in group))
            out["blocked"].append(sum(g.blocked for g in group))
            out["coalesced"].append(sum(g.coalesced for g in group))
            out["hedges_fired"].append(sum(g.hedges_fired for g in group))
            out["hedges_won_by_hedge"].append(sum(g.hedges_won_by_hedge for g in group))
            out["failovers"].append(sum(g.failovers for g in group))
            for g in group:
                backends.update(g.served_by)
            for stage in stages:
                values = [v for g in group for v in g.stage_ms.get(stage, [])]
                out[f"stage_{stage}"].append(_r(sum(values) / len(values)) if values else None)
        for backend in sorted(backends):
            out[f"served_{backend}"] = []
            for i in range(steps):
                lo = start + i * step_s
                out[f"served_{backend}"].append(
                    sum(
                        self.buckets[s].served_by.get(backend, 0)
                        for s in range(lo, lo + step_s)
                        if s in self.buckets
                    )
                )
        return {
            "window_s": window_s,
            "step_s": step_s,
            "backends": sorted(backends),
            "stages": list(stages),
            "series": dict(out),
        }

    def tokens_per_min_by_key(self, now: float | None = None) -> dict[str, int]:
        now = time.time() if now is None else now
        totals: dict[str, int] = defaultdict(int)
        for s in range(int(now) - 59, int(now) + 1):
            b = self.buckets.get(s)
            if b:
                for key, n in b.tokens_by_key.items():
                    totals[key] += n
        return dict(totals)

    def kpis(self, window_s: int = 60, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now

        def window(lo: int, hi: int) -> dict[str, Any]:
            group = [self.buckets[s] for s in range(lo, hi) if s in self.buckets]
            req = sum(g.requests for g in group)
            exact = sum(g.cache_exact for g in group)
            sem = sum(g.cache_semantic for g in group)
            lookups = exact + sem + sum(g.cache_miss for g in group)
            ttft = [v for g in group for v in g.ttft]
            return {
                "rps": round(req / (hi - lo), 3),
                "ttft_p95_ms": _r(percentile(ttft, 0.95)),
                "hit_rate": round((exact + sem) / lookups, 4) if lookups else None,
                "hit_rate_exact": round(exact / lookups, 4) if lookups else None,
                "hit_rate_semantic": round(sem / lookups, 4) if lookups else None,
                "saved_est_usd": round(sum(g.saved for g in group), 8),
                "requests": req,
            }

        end = int(now) + 1
        return {
            "window_s": window_s,
            "current": window(end - window_s, end),
            "previous": window(end - 2 * window_s, end - window_s),
        }


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
