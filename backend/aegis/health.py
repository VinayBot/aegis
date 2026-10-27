"""Real probes for every component, every `health_interval_seconds`.

A backend probe that fails feeds that backend's breaker as an error, but only when the backend
has not streamed a token in the last 2 s: busy is not broken. Probe successes never close an
open breaker; only a live request (the half-open probe) can do that.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import httpx

from aegis.state import AppState


def _result(
    ok: bool, detail: str, ms: float, status: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "status": status or ("up" if ok else "down"),
        "ok": ok,
        "detail": detail,
        "latency_ms": round(ms, 2),
        "checked_at": time.time(),
        **extra,
    }


class HealthMonitor:
    def __init__(self, state: AppState) -> None:
        self.s = state
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=1.0)
        self._last_published: dict[str, str] = {}

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):  # a probe bug must never stop health checks
                await self.check_all()
            await asyncio.sleep(self.s.settings.health_interval_seconds)

    async def check_all(self) -> dict[str, Any]:
        s = self.s
        cfg = s.config.get()
        s.router.sync(cfg)
        names = list(cfg.backends)
        backend_results = await asyncio.gather(*(self._backend(n) for n in names))
        results: dict[str, Any] = dict(
            zip((f"backend:{n}" for n in names), backend_results, strict=True)
        )
        results["redis"], results["jaeger"] = await asyncio.gather(self._redis(), self._jaeger())
        results["embedder"] = self._embedder()
        results["sqlite"] = self._sqlite()
        results["ws"] = _result(
            True,
            f"{s.bus.subscriber_count} live subscriber(s)",
            0.0,
            subscribers=s.bus.subscriber_count,
        )
        s.redis_ok = results["redis"]["ok"]
        s.health = results
        changed = {
            k: v["status"] for k, v in results.items() if self._last_published.get(k) != v["status"]
        }
        if changed:
            self._last_published.update(changed)
            s.bus.publish(
                "health",
                None,
                changed=changed,
                components={k: v["status"] for k, v in results.items()},
            )
        return results

    async def _backend(self, name: str) -> dict[str, Any]:
        rt = self.s.router.runtimes.get(name)
        if rt is None:
            return _result(False, "not configured", 0.0)
        ok, detail, ms = await rt.backend.probe()
        if not ok and not rt.recently_streaming() and "not installed" not in detail:
            rt.breaker.record_error(f"health probe: {detail}")
        breaker = rt.breaker.current_state()
        status = "up" if ok else "down"
        if ok and breaker != "closed":
            status = "degraded"
        if "not installed" in detail:
            status = "disabled"
        return _result(
            ok, detail, ms, status, breaker=breaker, in_flight=rt.in_flight, capacity=rt.capacity
        )

    async def _redis(self) -> dict[str, Any]:
        redis = self.s.redis
        if redis is None:
            return _result(False, "not configured", 0.0)
        start = time.perf_counter()
        try:
            await asyncio.wait_for(redis.ping(), 0.5)
            if not self.s.cache.index_ready:
                await self.s.cache.ensure_index()
            ms = (time.perf_counter() - start) * 1000
            return _result(
                True, f"PONG; vector index {'ready' if self.s.cache.index_ready else 'missing'}", ms
            )
        except Exception as exc:
            self.s.cache.index_ready = False
            return _result(
                False,
                f"{type(exc).__name__}: cache off, rate limits in-process",
                (time.perf_counter() - start) * 1000,
                "down",
            )

    async def _jaeger(self) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self.s.settings.jaeger_query_url}/api/services", timeout=0.8
            )
            ms = (time.perf_counter() - start) * 1000
            services = resp.json().get("data") or []
            return _result(resp.status_code == 200, f"query API up; services: {services}", ms)
        except Exception as exc:
            return _result(
                False,
                f"{type(exc).__name__}: traces are not being stored",
                (time.perf_counter() - start) * 1000,
            )

    def _embedder(self) -> dict[str, Any]:
        e = self.s.embedder
        if not e.loaded:
            return _result(False, "model not loaded yet", 0.0, "starting")
        last = e.latencies_ms[-1] if e.latencies_ms else 0.0
        return _result(True, f"{e.model_name} on CPU (in-process)", last)

    def _sqlite(self) -> dict[str, Any]:
        try:
            ms = self.s.ledger.ping()
            return _result(
                True,
                f"{self.s.ledger.rows_written} rows written, {self.s.ledger.pending} queued",
                ms,
            )
        except Exception as exc:
            return _result(False, f"{type(exc).__name__}", 0.0)
