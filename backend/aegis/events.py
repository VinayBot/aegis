"""The event bus: every request's lifecycle (stage entered, cache hit, hedge fired, breaker
changed...) is published here. The WebSocket streams it to the website, the metrics module
aggregates it, and the replay recorder saves it. Nothing on the website moves without an event.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self, history: int = 600, queue_size: int = 2000) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.recent: deque[dict[str, Any]] = deque(maxlen=history)
        self._queue_size = queue_size
        self._seq = 0
        self.dropped = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, type_: str, request_id: str | None = None, **data: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "seq": self._seq,
            "ts": time.time(),
            "type": type_,
            "request_id": request_id,
            **data,
        }
        self.recent.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped += 1
        return event

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
