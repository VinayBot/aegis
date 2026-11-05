"""Real probes for every component, every `health_interval_seconds`.

A backend probe that fails feeds that backend's breaker as an error, but only when the backend
has not streamed a token in the last 2 s: busy is not broken. Probe successes never close an
open breaker; only a live request (the half-open probe) can do that. When a breaker is half-open
and the backend idle, the monitor sends that live request itself (one real token), so an idle
gateway heals instead of waiting for traffic.

After the host pauses (laptop sleep: the wall clock jumps far past the check interval), every
probe times out at once. That is the host, not the backends, so probe failures are not counted
for a short grace period after a pause.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import httpx

from prahari.backends.base import BackendError
from prahari.engine import scrape
from prahari.router import BackendRuntime
from prahari.state import AppState

PAUSE_FACTOR = 5  # a gap this many intervals long (and at least PAUSE_MIN_S) means the host paused
PAUSE_MIN_S = 10.0
PAUSE_GRACE_S = 10.0


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
        self._last_round: float | None = None
        self._grace_until = 0.0
        self._probing: set[str] = set()
        self._probe_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for t in list(self._probe_tasks):
            t.cancel()
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
        now = time.time()
        gap = None if self._last_round is None else now - self._last_round
        self._last_round = now
        if gap is not None and gap > max(
            PAUSE_MIN_S, PAUSE_FACTOR * s.settings.health_interval_seconds
        ):
            self._grace_until = now + PAUSE_GRACE_S
            s.bus.publish("health.paused", None, gap_s=round(gap, 1), grace_s=PAUSE_GRACE_S)
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
        in_grace = time.time() < self._grace_until
        if (
            not ok
            and not rt.recently_streaming()
            and "not installed" not in detail
            and not in_grace
        ):
            rt.breaker.record_error(f"health probe: {detail}")
        breaker = rt.breaker.current_state()
        if ok and breaker == "half_open" and rt.in_flight == 0 and name not in self._probing:
            task = asyncio.get_running_loop().create_task(self._probe_request(name, rt))
            self._probe_tasks.add(task)
            task.add_done_callback(self._probe_tasks.discard)
        status = "up" if ok else "down"
        if ok and breaker != "closed":
            status = "degraded"
        if "not installed" in detail:
            status = "disabled"
        engine = None
        if ok and rt.cfg.engine_metrics and rt.cfg.base_url:
            # a missed read keeps the last stats; they go stale by themselves after 5 s
            rt.engine = await scrape(self._client, rt.cfg.base_url) or rt.engine
            engine = rt.engine.as_dict() if rt.engine else None
        return _result(
            ok,
            detail,
            ms,
            status,
            breaker=breaker,
            in_flight=rt.in_flight,
            capacity=rt.capacity,
            engine=engine,
            saturated=rt.saturated(),
        )

    async def _probe_request(self, name: str, rt: BackendRuntime) -> None:
        """The half-open probe for an idle backend: one real token through the (chaos-wrapped)
        backend. Live traffic that claims the probe first wins; this then does nothing."""
        if not rt.breaker.allow():
            return
        if not await rt.acquire(0):
            rt.breaker.release_probe()
            return
        self._probing.add(name)
        timeout = self.s.config.get().router.first_token_timeout_seconds
        payload = {
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        stream = rt.backend.stream(payload)
        try:
            await asyncio.wait_for(anext(stream), timeout)
            rt.breaker.record_success()
        except (TimeoutError, StopAsyncIteration):
            rt.breaker.record_timeout("idle probe: no first token")
        except BackendError as exc:
            rt.breaker.record_error(f"idle probe: {exc.kind}: {exc}")
        except Exception as exc:  # anything else still decides the probe, never leaves it claimed
            rt.breaker.record_error(f"idle probe: {type(exc).__name__}: {exc}")
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()  # type: ignore[attr-defined]
            rt.release()
            self._probing.discard(name)

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
