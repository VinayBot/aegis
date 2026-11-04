"""Backend selection, hedging and failover.

For each request, in the model's chain order:
- skip backends whose breaker is open;
- wait up to `slot_wait_seconds` for a free slot, else move on; nothing free within
  `queue_deadline_seconds` -> 503 + Retry-After. Busy is not broken: the first-token clock starts
  only once a slot is held.
- once streaming, if no first token arrives by the backend's rolling p95 TTFT, fire the same
  request at the next backend that has a free slot right now; the first token wins and the loser
  is cancelled mid-flight (its HTTP stream is closed, its slot released).
- before the first token, a connect error, 5xx, 429, unknown model or the first-token timeout
  (15 s + 1 s per 1k prompt tokens) fails over to the next backend and feeds its breaker.
- after the first token there is no failover: tokens already sent cannot be unsent. The relay
  raises MidStreamError and the pipeline ends the stream with an SSE error event.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Span

from aegis import telemetry
from aegis.backends.base import FAILOVER_KINDS, Backend, BackendError
from aegis.breaker import Breaker
from aegis.config import BackendConfig, GatewayConfig
from aegis.events import EventBus


class NoBackendAvailable(Exception):
    def __init__(self, message: str, retry_after: float, failovers: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.failovers = failovers


class UpstreamClientError(Exception):
    """The backend rejected the request itself (a 4xx other than 404/429): not a failover case."""

    def __init__(self, error: BackendError) -> None:
        super().__init__(str(error))
        self.error = error


class MidStreamError(Exception):
    def __init__(self, backend: str, error: BaseException) -> None:
        super().__init__(f"{backend}: {error}")
        self.backend = backend
        self.error = error


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass
class BackendRuntime:
    cfg: BackendConfig
    backend: Backend
    breaker: Breaker
    capacity: int
    semaphore: asyncio.Semaphore = field(init=False)
    in_flight: int = 0
    ttft_ms: deque[float] = field(default_factory=lambda: deque(maxlen=40))
    last_token_at: float = 0.0
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "attempts": 0,
            "first_tokens": 0,
            "errors": 0,
            "timeouts": 0,
            "hedges_fired": 0,
            "hedges_won": 0,
            "cancelled": 0,
            "midstream_errors": 0,
        }
    )

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(self.capacity)

    @property
    def name(self) -> str:
        return self.cfg.name

    @property
    def free_slots(self) -> int:
        return max(0, self.capacity - self.in_flight)

    async def acquire(self, timeout: float) -> bool:
        if timeout <= 0:
            if self.semaphore.locked():
                return False
            await self.semaphore.acquire()
        else:
            try:
                await asyncio.wait_for(self.semaphore.acquire(), timeout)
            except TimeoutError:
                return False
        self.in_flight += 1
        return True

    def release(self) -> None:
        self.in_flight -= 1
        self.semaphore.release()

    def p(self, q: float) -> float | None:
        return percentile(list(self.ttft_ms), q)

    def recently_streaming(self, within_s: float = 2.0) -> bool:
        return self.in_flight > 0 and time.monotonic() - self.last_token_at < within_s


@dataclass
class Attempt:
    rt: BackendRuntime
    hedge: bool
    started: float
    deadline: float
    span: Span
    queue: asyncio.Queue[tuple[str, Any]] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    getter: asyncio.Task[tuple[str, Any]] | None = None
    buffered: list[dict[str, Any]] = field(default_factory=list)
    is_probe: bool = False
    first_token_at: float | None = None


@dataclass
class Routed:
    attempt: Attempt
    ttft_ms: float
    failovers: list[dict[str, Any]]
    hedged: bool
    hedge_winner: str | None
    slot_wait_ms: float


def is_first_token(chunk: dict[str, Any]) -> bool:
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        # a tool call is the model's first output just as text is
        if delta.get("content") or delta.get("tool_calls") or choice.get("finish_reason"):
            return True
    return False


class Router:
    def __init__(
        self,
        bus: EventBus,
        wrap: Callable[[Backend], Backend],
        build: Callable[[BackendConfig], Backend],
    ) -> None:
        self.bus = bus
        self._wrap = wrap
        self._build = build
        self.runtimes: dict[str, BackendRuntime] = {}

    def sync(self, config: GatewayConfig) -> None:
        """Create runtimes for backends in the config; hot-reload slot counts when idle."""
        for name, cfg in config.backends.items():
            rt = self.runtimes.get(name)
            if rt is None:
                breaker = Breaker(
                    name,
                    config.breaker.error_threshold,
                    config.breaker.open_seconds,
                    on_change=self._on_breaker,
                )
                self.runtimes[name] = BackendRuntime(
                    cfg, self._wrap(self._build(cfg)), breaker, cfg.slots
                )
                continue
            rt.breaker.error_threshold = config.breaker.error_threshold
            rt.breaker.open_seconds = config.breaker.open_seconds
            if rt.capacity != cfg.slots and rt.in_flight == 0:
                rt.capacity = cfg.slots
                rt.semaphore = asyncio.Semaphore(cfg.slots)
            rt.cfg = cfg

    def _on_breaker(
        self, backend: str, previous: str, state: str, reason: str, retry_after: float | None
    ) -> None:
        self.bus.publish(
            "breaker.state",
            None,
            backend=backend,
            previous=previous,
            state=state,
            reason=reason,
            retry_after=retry_after,
        )

    def hedge_delay_ms(self, rt: BackendRuntime, config: GatewayConfig) -> float:
        p95 = rt.p(0.95) if len(rt.ttft_ms) >= 5 else None
        base = p95 if p95 is not None else config.router.hedge_default_ms
        return max(config.router.hedge_min_ms, base)

    async def aclose(self) -> None:
        for rt in self.runtimes.values():
            with contextlib.suppress(Exception):
                await rt.backend.aclose()

    # ------------------------------------------------------------------ selection

    async def _acquire(
        self,
        chain: list[str],
        tried: set[str],
        config: GatewayConfig,
        request_id: str,
        failovers: list[dict[str, Any]],
    ) -> tuple[BackendRuntime, float, bool]:
        start = time.monotonic()
        deadline = start + config.router.queue_deadline_seconds
        while True:
            candidates = [self.runtimes[n] for n in chain if n not in tried and n in self.runtimes]
            open_ones = [rt for rt in candidates if not rt.breaker.available()]
            usable = [rt for rt in candidates if rt.breaker.available()]
            if not usable:
                retry = min((rt.breaker.retry_after() for rt in open_ones), default=5.0)
                reason = "all breakers open" if open_ones and not tried else "every backend failed"
                raise NoBackendAvailable(reason, max(1.0, retry), failovers)
            for rt in usable:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                wait = min(config.router.slot_wait_seconds, remaining)
                if rt.free_slots == 0:
                    self.bus.publish(
                        "slot.wait",
                        request_id,
                        backend=rt.name,
                        wait_s=round(wait, 2),
                        in_flight=rt.in_flight,
                        capacity=rt.capacity,
                    )
                if await rt.acquire(wait):
                    probe = rt.breaker.current_state() == "half_open"
                    if not rt.breaker.allow():
                        rt.release()
                        continue
                    return rt, (time.monotonic() - start) * 1000, probe
                self.bus.publish(
                    "slot.skip",
                    request_id,
                    backend=rt.name,
                    reason=f"no free slot within {wait:.1f}s",
                )
            if time.monotonic() >= deadline:
                raise NoBackendAvailable(
                    f"no backend slot free within {config.router.queue_deadline_seconds:.0f}s",
                    2.0,
                    failovers,
                )

    def _try_hedge_backend(self, chain: list[str], busy: set[str]) -> BackendRuntime | None:
        for name in chain:
            rt = self.runtimes.get(name)
            if rt is None or name in busy or rt.free_slots == 0:
                continue
            if rt.breaker.current_state() != "closed":
                continue
            if not rt.semaphore.locked():
                return rt
        return None

    # ------------------------------------------------------------------ attempts

    def _launch(
        self,
        rt: BackendRuntime,
        payload: dict[str, Any],
        params: dict[str, Any],
        root: Span,
        prompt_tokens: int,
        config: GatewayConfig,
        hedge: bool,
        is_probe: bool,
        request_id: str,
    ) -> Attempt:
        now = time.monotonic()
        timeout = config.router.first_token_timeout_seconds + prompt_tokens / 1000.0
        span = telemetry.start_genai_span(
            root, rt.name, rt.backend.provider, rt.backend.upstream_model, params, hedge=hedge
        )
        attempt = Attempt(
            rt=rt, hedge=hedge, started=now, deadline=now + timeout, span=span, is_probe=is_probe
        )
        rt.counters["attempts"] += 1
        if hedge:
            rt.counters["hedges_fired"] += 1

        async def run() -> None:
            try:
                async for chunk in rt.backend.stream(payload):
                    rt.last_token_at = time.monotonic()
                    await attempt.queue.put(("chunk", chunk))
                await attempt.queue.put(("done", None))
            except asyncio.CancelledError:
                raise
            except BackendError as exc:
                await attempt.queue.put(("error", exc))
            except Exception as exc:
                await attempt.queue.put(
                    ("error", BackendError("stream", f"{type(exc).__name__}: {exc}"))
                )
            finally:
                rt.release()

        attempt.task = asyncio.get_running_loop().create_task(run())
        self.bus.publish(
            "backend.try",
            request_id,
            backend=rt.name,
            hedge=hedge,
            probe=is_probe,
            timeout_s=round(timeout, 1),
        )
        return attempt

    def _cancel(self, attempt: Attempt, reason: str) -> None:
        if attempt.getter is not None:
            attempt.getter.cancel()
        if attempt.task is not None and not attempt.task.done():
            attempt.task.cancel()
        if attempt.is_probe:
            attempt.rt.breaker.release_probe()
        attempt.rt.counters["cancelled"] += 1
        telemetry.end_error(attempt.span, reason, **{"aegis.cancelled": True})

    async def route(
        self,
        *,
        config: GatewayConfig,
        model: str,
        payload: dict[str, Any],
        params: dict[str, Any],
        root: Span,
        prompt_tokens: int,
        request_id: str,
    ) -> Routed:
        chain = config.models[model].chain
        failovers: list[dict[str, Any]] = []
        tried: set[str] = set()
        attempts: list[Attempt] = []
        hedged = False  # a hedge request was actually fired
        hedge_checked = False  # the hedge timer has fired for the current primary
        slot_wait_ms = 0.0

        async def launch_next() -> None:
            nonlocal slot_wait_ms
            rt, waited, probe = await self._acquire(chain, tried, config, request_id, failovers)
            slot_wait_ms += waited
            tried.add(rt.name)
            attempts.append(
                self._launch(
                    rt,
                    payload,
                    params,
                    root,
                    prompt_tokens,
                    config,
                    hedge=False,
                    is_probe=probe,
                    request_id=request_id,
                )
            )

        await launch_next()
        hedge_at = attempts[0].started + self.hedge_delay_ms(attempts[0].rt, config) / 1000.0

        def fail(attempt: Attempt, error: BackendError) -> None:
            attempts.remove(attempt)
            rt = attempt.rt
            if attempt.getter is not None:
                attempt.getter.cancel()
            if error.kind == "timeout":
                rt.counters["timeouts"] += 1
                rt.breaker.record_timeout(str(error))
                if attempt.task is not None:
                    attempt.task.cancel()
            else:
                rt.counters["errors"] += 1
                rt.breaker.record_error(f"{error.kind}: {error}")
            telemetry.end_error(attempt.span, str(error), **{"aegis.failure": error.kind})
            failovers.append({"backend": rt.name, "reason": error.kind, "detail": str(error)[:160]})
            self.bus.publish(
                "failover",
                request_id,
                backend=rt.name,
                reason=error.kind,
                detail=str(error)[:160],
                hedge=attempt.hedge,
            )
            if error.kind not in FAILOVER_KINDS:
                raise UpstreamClientError(error)  # the outer handler cancels the others

        try:
            while True:
                for a in attempts:
                    if a.getter is None or a.getter.done():
                        a.getter = asyncio.get_running_loop().create_task(a.queue.get())
                now = time.monotonic()
                wake = min(a.deadline for a in attempts)
                if not hedge_checked:
                    wake = min(wake, hedge_at)
                done, _ = await asyncio.wait(
                    [a.getter for a in attempts if a.getter],
                    timeout=max(0.0, wake - now),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                winner: Attempt | None = None
                for a in list(attempts):
                    if a.getter not in done or a.getter is None:
                        continue
                    kind, value = a.getter.result()
                    a.getter = None
                    if kind == "chunk":
                        a.buffered.append(value)
                        if is_first_token(value):
                            winner = a
                            break
                    elif kind == "done":
                        a.queue.put_nowait(("done", None))  # let the relay see the end too
                        winner = a
                        break
                    else:
                        fail(a, value)
                if winner is not None:
                    winner.first_token_at = time.monotonic()
                    ttft = (winner.first_token_at - winner.started) * 1000
                    rt = winner.rt
                    rt.ttft_ms.append(ttft)
                    rt.counters["first_tokens"] += 1
                    rt.breaker.record_success()
                    if winner.getter is not None:
                        winner.getter.cancel()
                        winner.getter = None
                    losers = [a for a in attempts if a is not winner]
                    for loser in losers:
                        self._cancel(loser, f"lost hedge race to {rt.name}")
                    hedge_winner = None
                    if hedged:
                        hedge_winner = rt.name
                        if winner.hedge:
                            rt.counters["hedges_won"] += 1
                        self.bus.publish(
                            "hedge.win",
                            request_id,
                            winner=rt.name,
                            losers=[a.rt.name for a in losers],
                            ttft_ms=round(ttft, 1),
                        )
                    self.bus.publish(
                        "backend.first_token",
                        request_id,
                        backend=rt.name,
                        ttft_ms=round(ttft, 1),
                        hedge=winner.hedge,
                    )
                    return Routed(winner, ttft, failovers, hedged, hedge_winner, slot_wait_ms)

                now = time.monotonic()
                for a in list(attempts):
                    if now >= a.deadline:
                        fail(
                            a,
                            BackendError(
                                "timeout", f"no first token within {a.deadline - a.started:.1f}s"
                            ),
                        )
                if not hedge_checked and attempts and now >= hedge_at:
                    hedge_checked = True
                    busy = {a.rt.name for a in attempts}
                    target = self._try_hedge_backend(chain, busy | tried)
                    if target is not None and await target.acquire(0):
                        hedged = True
                        tried.add(target.name)
                        primary = attempts[0].rt.name
                        self.bus.publish(
                            "hedge.fire",
                            request_id,
                            primary=primary,
                            hedge=target.name,
                            after_ms=round((now - attempts[0].started) * 1000, 1),
                        )
                        attempts.append(
                            self._launch(
                                target,
                                payload,
                                params,
                                root,
                                prompt_tokens,
                                config,
                                hedge=True,
                                is_probe=False,
                                request_id=request_id,
                            )
                        )
                    else:
                        self.bus.publish(
                            "hedge.skip",
                            request_id,
                            primary=attempts[0].rt.name,
                            reason="no other backend with a free slot",
                        )
                if not attempts:
                    await launch_next()
                    hedge_at = (
                        attempts[0].started + self.hedge_delay_ms(attempts[0].rt, config) / 1000.0
                    )
                    hedge_checked = hedged
        except BaseException:
            for a in list(attempts):
                self._cancel(a, "request abandoned")
            raise

    async def relay(self, routed: Routed) -> AsyncIterator[dict[str, Any]]:
        attempt = routed.attempt
        rt = attempt.rt
        try:
            for chunk in attempt.buffered:
                yield chunk
            while True:
                kind, value = await attempt.queue.get()
                if kind == "chunk":
                    yield value
                elif kind == "done":
                    return
                else:
                    rt.counters["midstream_errors"] += 1
                    rt.breaker.record_error(f"mid-stream: {value}")
                    raise MidStreamError(rt.name, value)
        finally:
            if attempt.task is not None and not attempt.task.done():
                attempt.task.cancel()
