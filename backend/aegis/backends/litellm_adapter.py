"""LiteLLM as one optional backend: `uv sync --extra litellm`.

It is a backend like any other, so the router's slots, breakers, hedging and failover still own
reliability; LiteLLM only translates the call. Why the core path does not use LiteLLM is in
docs/DECISIONS.md.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from aegis.backends.base import BackendError
from aegis.config import BackendConfig


def litellm_available() -> bool:
    try:
        import litellm  # noqa: F401
    except Exception:
        return False
    return True


class LiteLLMBackend:
    def __init__(self, cfg: BackendConfig) -> None:
        self.name = cfg.name
        self.provider = "litellm"
        self.upstream_model = cfg.model
        self.api_base = cfg.api_base or None

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        try:
            import litellm
        except Exception as exc:
            raise BackendError(
                "connect", "litellm is not installed (uv sync --extra litellm)"
            ) from exc
        params = {
            k: v for k, v in payload.items() if k not in ("model", "stream", "stream_options")
        }
        try:
            response = await litellm.acompletion(
                model=self.upstream_model,
                api_base=self.api_base,
                stream=True,
                stream_options={"include_usage": True},
                num_retries=0,
                timeout=60,
                **params,
            )
            async for chunk in response:
                yield chunk.model_dump(exclude_none=True)
        except BackendError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            status = getattr(exc, "status_code", None)
            if "Connection" in name or "APIConnection" in name:
                raise BackendError("connect", f"{name}: {exc}"[:200]) from exc
            if "RateLimit" in name or status == 429:
                raise BackendError("http_429", f"{name}"[:200], 429) from exc
            if "NotFound" in name or status == 404:
                raise BackendError("unknown_model", f"{name}: {exc}"[:200], 404) from exc
            if "Timeout" in name:
                raise BackendError("timeout", name) from exc
            raise BackendError("http_5xx", f"{name}: {exc}"[:200], status) from exc

    async def probe(self) -> tuple[bool, str, float]:
        start = time.perf_counter()
        if not litellm_available():
            return False, "litellm not installed (optional: uv sync --extra litellm)", 0.0
        import httpx

        base = (self.api_base or "").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=0.4) as client:
                resp = await client.get(f"{base}/api/tags")
            ok = resp.status_code == 200
            return (
                ok,
                f"litellm -> {base} HTTP {resp.status_code}",
                (time.perf_counter() - start) * 1000,
            )
        except Exception as exc:
            return False, f"{type(exc).__name__}"[:200], (time.perf_counter() - start) * 1000

    async def aclose(self) -> None:
        return None
