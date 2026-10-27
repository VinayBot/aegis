"""What the router needs from a backend: stream chat-completion chunks, or raise BackendError."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

FailureKind = Literal[
    "connect", "timeout", "http_5xx", "http_429", "unknown_model", "http_4xx", "stream", "chaos"
]

# Failures that move the request to the next backend (before the first token).
FAILOVER_KINDS: frozenset[str] = frozenset(
    {"connect", "timeout", "http_5xx", "http_429", "unknown_model", "chaos"}
)


class BackendError(Exception):
    def __init__(self, kind: FailureKind, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


class Backend(Protocol):
    name: str
    provider: str
    upstream_model: str

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yields OpenAI `chat.completion.chunk` dicts, the last one carrying `usage` if known."""
        ...

    async def probe(self) -> tuple[bool, str, float]:
        """Cheap liveness check: (ok, detail, latency_ms)."""
        ...

    async def aclose(self) -> None: ...
