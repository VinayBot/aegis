"""Everything the gateway holds for its lifetime, built once at startup."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis

from aegis.backends.base import Backend
from aegis.backends.litellm_adapter import LiteLLMBackend
from aegis.backends.openai_compat import OpenAICompatBackend
from aegis.budget import Budgets
from aegis.cache import ResponseCache
from aegis.chaos import ChaosController
from aegis.coalesce import Coalescer
from aegis.config import BackendConfig, ConfigStore
from aegis.embedder import Embedder
from aegis.events import EventBus
from aegis.guardrails.safety import SafetyClassifier
from aegis.ledger import Ledger
from aegis.metrics import Metrics
from aegis.ratelimit import RateLimiter
from aegis.router import Router
from aegis.settings import Settings

log = logging.getLogger("aegis")


def build_backend(cfg: BackendConfig) -> Backend:
    if cfg.kind == "litellm":
        return LiteLLMBackend(cfg)
    return OpenAICompatBackend(cfg)


class Recorder:
    """Background queue for work that must not sit in the request path: cache writes, budget
    charges and rate-limit settlement. (Ledger rows have their own batched queue.)"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Callable[[], Awaitable[Any]]] = asyncio.Queue(maxsize=10000)
        self._task: asyncio.Task[None] | None = None
        self.done = 0
        self.failed = 0

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    def submit(self, job: Callable[[], Awaitable[Any]]) -> None:
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self.failed += 1

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def drain(self) -> None:
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await job()
                self.done += 1
            except Exception as exc:
                self.failed += 1
                log.warning("background job failed: %s", exc)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


@dataclass
class AppState:
    settings: Settings
    config: ConfigStore
    bus: EventBus
    redis: Redis | None
    cache: ResponseCache
    limiter: RateLimiter
    budgets: Budgets
    embedder: Embedder
    coalescer: Coalescer
    router: Router
    chaos: ChaosController
    ledger: Ledger
    metrics: Metrics
    recorder: Recorder
    redis_ok: bool = True
    health: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    safety: SafetyClassifier = field(default_factory=SafetyClassifier)
