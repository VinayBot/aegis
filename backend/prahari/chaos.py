"""Dev-only fault injection for the Chaos Lab. Off unless PRAHARI_CHAOS_ENABLED=1.

- latency: added before a backend's first byte (and to its health probe).
- error rate: that fraction of calls fail with an upstream 503 before any token.
- kill: if the gateway owns the backend's process (a pid file in var/run), it is really frozen
  with SIGSTOP and thawed with SIGCONT, so requests genuinely hang and probes genuinely time out.
  Otherwise (e.g. the system Ollama app) connections are refused at the gateway's edge.

Every action is logged at WARNING, appended to the action log, published as an event, and
reverts by itself after `auto_revert_seconds`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import signal
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from prahari.backends.base import Backend, BackendError
from prahari.events import EventBus

log = logging.getLogger("prahari.chaos")


@dataclass
class Fault:
    latency_ms: int = 0
    error_rate: float = 0.0
    killed: bool = False
    kill_mode: str | None = None  # "sigstop" | "refuse"
    pid: int | None = None
    expires: dict[str, float] = field(default_factory=dict)


class ChaosController:
    def __init__(self, bus: EventBus, run_dir: Path, enabled: bool, auto_revert_s: float) -> None:
        self.bus = bus
        self.run_dir = run_dir
        self.enabled = enabled
        self.auto_revert_s = auto_revert_s
        self.faults: dict[str, Fault] = {}
        self.log: deque[dict[str, Any]] = deque(maxlen=100)
        self._timers: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._rng = random.Random()

    def fault(self, backend: str) -> Fault:
        return self.faults.setdefault(backend, Fault())

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_revert_seconds": self.auto_revert_s,
            "faults": {k: asdict(v) for k, v in self.faults.items()},
            "log": list(self.log),
        }

    def _record(self, action: str, backend: str, **detail: Any) -> dict[str, Any]:
        entry = {"ts": time.time(), "action": action, "backend": backend, **detail}
        self.log.appendleft(entry)
        log.warning("CHAOS %s %s %s", action, backend, detail)
        self.bus.publish("chaos.action", None, **entry)
        return entry

    def _schedule_revert(self, backend: str, what: str, seconds: float | None) -> float:
        seconds = self.auto_revert_s if seconds is None else seconds
        key = (backend, what)
        if (task := self._timers.pop(key, None)) is not None:
            task.cancel()

        async def revert() -> None:
            await asyncio.sleep(seconds)
            self._timers.pop(key, None)
            self.apply(backend, what, None, auto=True)

        self._timers[key] = asyncio.get_running_loop().create_task(revert())
        expires = time.time() + seconds
        self.fault(backend).expires[what] = expires
        return expires

    def _pid(self, process: str | None) -> int | None:
        if not process:
            return None
        try:
            pid = int((self.run_dir / f"{process}.pid").read_text().strip())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return None

    def apply(
        self,
        backend: str,
        what: str,
        value: Any,
        process: str | None = None,
        auto: bool = False,
        revert_after: float | None = None,
    ) -> dict[str, Any]:
        """what: latency | error_rate | kill. value None (or 0/False) clears that fault."""
        if not self.enabled:
            raise PermissionError("chaos is disabled (set PRAHARI_CHAOS_ENABLED=1, dev only)")
        f = self.fault(backend)
        active = bool(value)
        if what == "latency":
            f.latency_ms = int(value or 0)
        elif what == "error_rate":
            f.error_rate = max(0.0, min(1.0, float(value or 0)))
        elif what == "kill":
            if active and not f.killed:
                pid = self._pid(process)
                if pid is not None:
                    os.kill(pid, signal.SIGSTOP)
                    f.kill_mode, f.pid = "sigstop", pid
                else:
                    f.kill_mode, f.pid = "refuse", None
                f.killed = True
            elif not active and f.killed:
                if f.kill_mode == "sigstop" and f.pid is not None:
                    with contextlib.suppress(OSError):
                        os.kill(f.pid, signal.SIGCONT)
                f.killed, f.kill_mode, f.pid = False, None, None
        else:
            raise ValueError(f"unknown chaos action {what!r}")
        expires = None
        if active:
            expires = self._schedule_revert(backend, what, revert_after)
        else:
            f.expires.pop(what, None)
            if not auto and (task := self._timers.pop((backend, what), None)) is not None:
                task.cancel()
        return self._record(
            ("auto-revert " if auto else "") + (what if active else f"clear {what}"),
            backend,
            value=value,
            mode=f.kill_mode,
            expires=expires,
        )

    def revert_all(self) -> None:
        for backend, f in list(self.faults.items()):
            for what, on in (
                ("kill", f.killed),
                ("latency", f.latency_ms),
                ("error_rate", f.error_rate),
            ):
                if on:
                    self.apply(backend, what, None)

    async def shutdown(self) -> None:
        """Never leave a model server frozen behind us."""
        for f in self.faults.values():
            if f.kill_mode == "sigstop" and f.pid is not None:
                with contextlib.suppress(OSError):
                    os.kill(f.pid, signal.SIGCONT)
        for task in self._timers.values():
            task.cancel()

    def wrap(self, backend: Backend) -> ChaosBackend:
        return ChaosBackend(backend, self)


class ChaosBackend:
    """Applies the controller's faults in front of a real backend."""

    def __init__(self, inner: Backend, controller: ChaosController) -> None:
        self.inner = inner
        self.controller = controller
        self.name = inner.name
        self.provider = inner.provider
        self.upstream_model = inner.upstream_model

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        f = self.controller.faults.get(self.name)
        if f is not None:
            if f.killed and f.kill_mode == "refuse":
                raise BackendError("connect", "connection refused (chaos: backend killed)")
            if f.latency_ms:
                await asyncio.sleep(f.latency_ms / 1000)
            if f.error_rate and self.controller._rng.random() < f.error_rate:
                raise BackendError("http_5xx", "HTTP 503 (chaos: injected error)", 503)
        async for chunk in self.inner.stream(payload):
            yield chunk

    async def probe(self) -> tuple[bool, str, float]:
        f = self.controller.faults.get(self.name)
        if f is not None and f.killed and f.kill_mode == "refuse":
            return False, "connection refused (chaos: backend killed)", 0.0
        ok, detail, ms = await self.inner.probe()
        if f is not None and f.latency_ms:
            detail += f" (+{f.latency_ms} ms injected on requests)"
        return ok, detail, ms

    async def aclose(self) -> None:
        await self.inner.aclose()
