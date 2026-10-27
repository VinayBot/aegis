"""Request coalescing (singleflight): identical requests in flight at the same moment share one
upstream call. The first becomes the leader; the rest follow its stream chunk by chunk, from the
beginning, however late they join.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Flight:
    key: str
    leader_id: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)  # first token or failure
    finished: bool = False
    error: BaseException | None = None
    followers: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)  # backend, ttft... for followers
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def push(self, chunk: dict[str, Any]) -> None:
        async with self._changed:
            self.chunks.append(chunk)
            self._changed.notify_all()

    async def finish(self, error: BaseException | None = None) -> None:
        async with self._changed:
            self.finished = True
            self.error = error
            self.started.set()
            self._changed.notify_all()

    async def follow(self) -> AsyncIterator[dict[str, Any]]:
        index = 0
        while True:
            async with self._changed:
                while index >= len(self.chunks) and not self.finished:
                    await self._changed.wait()
                pending = self.chunks[index:]
                done = self.finished
                error = self.error
            for chunk in pending:
                yield chunk
            index += len(pending)
            if done and index >= len(self.chunks):
                if error is not None:
                    raise error
                return


class Coalescer:
    def __init__(self) -> None:
        self.flights: dict[str, Flight] = {}
        self.coalesced_total = 0

    def join(self, key: str, request_id: str) -> tuple[Flight, bool]:
        """Returns (flight, is_leader)."""
        flight = self.flights.get(key)
        if flight is not None and not flight.finished:
            flight.followers.append(request_id)
            self.coalesced_total += 1
            return flight, False
        flight = Flight(key=key, leader_id=request_id)
        self.flights[key] = flight
        return flight, True

    def release(self, flight: Flight) -> None:
        if self.flights.get(flight.key) is flight:
            del self.flights[flight.key]
