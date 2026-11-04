"""Circuit breakers, one per backend.

closed -> open, on whichever comes first:
  - one first-token timeout (a frozen backend must not hold requests for 15 s each);
  - `error_threshold` consecutive errors (fast: a dead backend trips in three requests);
  - a failure rate of at least `failure_rate` over the last `window_seconds`, once that window
    holds `min_requests` outcomes (the classic rule: a backend failing every other request never
    has three consecutive errors, yet is clearly unhealthy).
open -> half_open: after `open_seconds`; exactly one live request is let through as a probe.
half_open -> closed when the probe streams a first token; -> open again if it fails.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

State = Literal["closed", "open", "half_open"]


@dataclass
class Breaker:
    backend: str
    error_threshold: int = 3
    open_seconds: float = 30.0
    on_change: Callable[[str, State, State, str, float | None], None] | None = None
    clock: Callable[[], float] = time.monotonic
    state: State = "closed"
    consecutive_errors: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False
    trips: int = 0
    failure_rate: float = 0.5
    window_seconds: float = 30.0
    min_requests: int = 10
    outcomes: deque[tuple[float, bool]] = field(default_factory=deque)  # (time, failed)
    history: list[tuple[float, State, str]] = field(default_factory=list)

    @property
    def reopens_at(self) -> float | None:
        if self.state == "open" and self.opened_at is not None:
            return self.opened_at + self.open_seconds
        return None

    def retry_after(self) -> float:
        at = self.reopens_at
        return max(0.0, at - self.clock()) if at is not None else 0.0

    def current_state(self) -> State:
        """The state with the open->half_open timer applied (no side effects on probes)."""
        if self.state == "open" and self.retry_after() <= 0:
            self._set("half_open", "cool-down elapsed")
        return self.state

    def allow(self) -> bool:
        """May a request use this backend now? In half_open only the first caller gets through."""
        state = self.current_state()
        if state == "closed":
            return True
        if state == "half_open" and not self.probe_in_flight:
            self.probe_in_flight = True
            return True
        return False

    def available(self) -> bool:
        """Like allow() but read-only: used to list candidates without claiming the probe."""
        state = self.current_state()
        return state == "closed" or (state == "half_open" and not self.probe_in_flight)

    def release_probe(self) -> None:
        """The probe request never reached the backend (e.g. it lost a slot race)."""
        self.probe_in_flight = False

    def _observe(self, failed: bool) -> float | None:
        """Record an outcome; return the window's failure rate once it is large enough."""
        now = self.clock()
        self.outcomes.append((now, failed))
        while self.outcomes and self.outcomes[0][0] < now - self.window_seconds:
            self.outcomes.popleft()
        if len(self.outcomes) < self.min_requests:
            return None
        return sum(f for _, f in self.outcomes) / len(self.outcomes)

    def window_failure_rate(self) -> float | None:
        now = self.clock()
        recent = [f for t, f in self.outcomes if t >= now - self.window_seconds]
        return sum(recent) / len(recent) if recent else None

    def record_success(self) -> None:
        self._observe(False)
        self.consecutive_errors = 0
        if self.state != "closed":
            self.probe_in_flight = False
            self._set("closed", "probe succeeded")

    def record_error(self, reason: str) -> None:
        if self.state == "half_open":
            self.probe_in_flight = False
            self._open(f"probe failed: {reason}")
            return
        if self.state == "open":
            return
        rate = self._observe(True)
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.error_threshold:
            self._open(f"{self.consecutive_errors} consecutive errors: {reason}")
        elif rate is not None and rate >= self.failure_rate:
            self._open(
                f"failure rate {rate:.0%} over {self.window_seconds:g} s "
                f"({len(self.outcomes)} requests): {reason}"
            )

    def record_timeout(self, reason: str = "first-token timeout") -> None:
        if self.state == "open":
            return
        self.probe_in_flight = False
        self._open(reason)

    def force(self, state: State, reason: str) -> None:
        if state == "open":
            self._open(reason)
        else:
            self.consecutive_errors = 0
            self.probe_in_flight = False
            self._set(state, reason)

    def _open(self, reason: str) -> None:
        self.opened_at = self.clock()
        self.trips += 1
        self.consecutive_errors = 0
        self.outcomes.clear()  # a fresh window after recovery
        self._set("open", reason)

    def _set(self, state: State, reason: str) -> None:
        if state == self.state and state != "open":
            return
        previous = self.state
        self.state = state
        self.history.append((time.time(), state, reason))
        del self.history[:-200]
        if self.on_change:
            self.on_change(
                self.backend, previous, state, reason, self.reopens_at and self.retry_after()
            )
