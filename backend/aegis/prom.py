"""Prometheus metrics at GET /metrics (text exposition format), fed from the event bus.

The same lifecycle events that drive the website drive these series, so a Grafana panel and the
Control Room can never disagree. Labels are bounded (model, backend, cache outcome, status,
tenant); request ids and trace ids are never labels.

Gauges that describe the present (breaker state, slots in use) are read from the router at scrape
time instead of being kept in sync by events.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from aegis.events import EventBus

BREAKER_VALUE = {"closed": 0, "half_open": 1, "open": 2}
# events that mean "refused before any model", by reason
REJECTIONS = {
    "ratelimit.reject": "rate_limit",
    "budget.block": "budget",
    "guardrail.block": "guardrail",
    "safety.block": "safety",
}
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40)

__all__ = ["CONTENT_TYPE_LATEST", "PromMetrics"]


class PromMetrics:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.registry = CollectorRegistry(auto_describe=True)
        r = self.registry
        self.requests = Counter(
            "aegis_requests",
            "Requests finished, by outcome.",
            ["model", "backend", "cache", "status"],
            registry=r,
        )
        self.latency = Histogram(
            "aegis_request_duration_seconds",
            "End-to-end request latency.",
            ["model", "cache"],
            buckets=LATENCY_BUCKETS,
            registry=r,
        )
        self.ttft = Histogram(
            "aegis_time_to_first_token_seconds",
            "Time to the first token from a model (cache hits excluded).",
            ["backend"],
            buckets=LATENCY_BUCKETS,
            registry=r,
        )
        self.tokens = Counter(
            "aegis_tokens", "Tokens processed.", ["direction", "backend"], registry=r
        )
        self.cost = Counter(
            "aegis_cost_est_usd",
            "Estimated cloud-equivalent spend (local inference has no bill).",
            ["tenant"],
            registry=r,
        )
        self.saved = Counter(
            "aegis_saved_est_usd",
            "Estimated cloud-equivalent spend avoided by caches and coalescing.",
            ["tenant"],
            registry=r,
        )
        self.rejections = Counter(
            "aegis_rejections",
            "Requests refused before any model: rate limit, budget, guardrail.",
            ["reason"],
            registry=r,
        )
        self.failovers = Counter(
            "aegis_failovers",
            "Backends skipped after a failure before the first token.",
            registry=r,
        )
        self.hedges = Counter("aegis_hedges", "Hedged requests, by result.", ["result"], registry=r)
        self.retries = Counter(
            "aegis_chain_retries", "Whole-chain retries after backoff.", registry=r
        )
        self.breaker_transitions = Counter(
            "aegis_breaker_transitions",
            "Circuit breaker state changes.",
            ["backend", "to"],
            registry=r,
        )
        self.feedback = Counter(
            "aegis_feedback",
            "User ratings of answers (thumbs up/down).",
            ["model", "backend", "rating"],
            registry=r,
        )
        self.redactions = Counter(
            "aegis_pii_redactions", "PII values redacted, by entity type.", ["entity"], registry=r
        )
        self.breaker = Gauge(
            "aegis_breaker_state",
            "Circuit breaker state: 0 closed, 1 half-open, 2 open.",
            ["backend"],
            registry=r,
        )
        self.in_flight = Gauge(
            "aegis_backend_in_flight", "Requests holding a backend slot.", ["backend"], registry=r
        )
        self.slots = Gauge(
            "aegis_backend_slots", "Backend concurrency slots.", ["backend"], registry=r
        )
        self._task: asyncio.Task[None] | None = None

    # ----------------------------------------------------------------- events -> series

    def observe(self, e: dict[str, Any]) -> None:
        kind = str(e.get("type"))
        if kind == "request.end":
            self._request_end(e)
        elif kind in REJECTIONS:
            self.rejections.labels(REJECTIONS[kind]).inc()
        elif kind == "failover":
            self.failovers.inc()
        elif kind == "router.retry":
            self.retries.inc()
        elif kind == "feedback":
            self.feedback.labels(
                str(e.get("model") or "?"), str(e.get("backend") or "none"), str(e.get("rating"))
            ).inc()
        elif kind == "breaker.state":
            self.breaker_transitions.labels(str(e.get("backend")), str(e.get("state"))).inc()

    def _request_end(self, e: dict[str, Any]) -> None:
        model, backend = str(e.get("model") or "?"), str(e.get("backend") or "none")
        cache, status = str(e.get("cache") or "miss"), str(e.get("status") or "?")
        self.requests.labels(model, backend, cache, status).inc()
        if e.get("latency_ms") is not None:
            self.latency.labels(model, cache).observe(float(e["latency_ms"]) / 1000)
        reached_model = cache in ("miss", "bypass")
        if reached_model and e.get("ttft_ms") is not None and not e.get("coalesced"):
            self.ttft.labels(backend).observe(float(e["ttft_ms"]) / 1000)
        if reached_model:
            self.tokens.labels("in", backend).inc(int(e.get("tokens_in") or 0))
            self.tokens.labels("out", backend).inc(int(e.get("tokens_out") or 0))
        self._money(str(e.get("tenant") or "?"), e)
        if e.get("hedged"):
            self.hedges.labels("won_by_hedge" if e.get("hedge_won") else "won_by_primary").inc()
        for entity, n in (e.get("redactions") or {}).items():
            self.redactions.labels(entity).inc(int(n))

    def _money(self, tenant: str, e: dict[str, Any]) -> None:
        if e.get("cost_est_usd"):
            self.cost.labels(tenant).inc(float(e["cost_est_usd"]))
        if e.get("saved_est_usd"):
            self.saved.labels(tenant).inc(float(e["saved_est_usd"]))

    async def _run(self) -> None:
        async with self.bus.subscribe() as queue:
            while True:
                event = await queue.get()
                with contextlib.suppress(Exception):  # one bad event never stops the exporter
                    self.observe(event)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ----------------------------------------------------------------- scrape

    def render(self, runtimes: dict[str, Any]) -> bytes:
        for name, rt in runtimes.items():
            self.breaker.labels(name).set(BREAKER_VALUE.get(rt.breaker.current_state(), -1))
            self.in_flight.labels(name).set(rt.in_flight)
            self.slots.labels(name).set(rt.capacity)
        return generate_latest(self.registry)
