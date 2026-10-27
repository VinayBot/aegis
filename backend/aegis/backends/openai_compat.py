"""Any OpenAI-compatible server (mlx_lm.server, Ollama, vLLM, llama.cpp...) over raw httpx.

The core path uses httpx rather than an SDK so every failure is visible to the router and the
trace: connect errors, status codes, keep-alive comments and malformed lines are all handled
here, in the open.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from aegis.backends.base import BackendError
from aegis.config import BackendConfig


class OpenAICompatBackend:
    def __init__(self, cfg: BackendConfig, client: httpx.AsyncClient | None = None) -> None:
        self.name = cfg.name
        self.provider = cfg.provider
        self.upstream_model = cfg.model
        self.base_url = cfg.base_url.rstrip("/")
        self.api_key = cfg.api_key
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=60.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        body = {
            **payload,
            "model": self.upstream_model,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/chat/completions", json=body, headers=self._headers()
            ) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode(errors="replace")[:300]
                    raise _status_error(resp.status_code, text)
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):  # blank separators, keep-alive comments
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise BackendError("stream", f"malformed chunk: {data[:80]}") from exc
                    if "error" in chunk:
                        raise BackendError("stream", str(chunk["error"])[:200])
                    yield chunk
        except BackendError:
            raise
        except httpx.ConnectError as exc:
            raise BackendError("connect", f"connect failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise BackendError("timeout", f"{type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise BackendError("stream", f"{type(exc).__name__}: {exc}") from exc

    async def probe(self) -> tuple[bool, str, float]:
        start = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=0.4
            )
            ms = (time.perf_counter() - start) * 1000
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}", ms
            ids = [m.get("id") for m in resp.json().get("data", [])]
            if self.upstream_model not in ids:
                return False, f"model {self.upstream_model} not served (has {ids[:4]})", ms
            return True, f"serving {self.upstream_model}", ms
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:200], (time.perf_counter() - start) * 1000

    async def aclose(self) -> None:
        await self._client.aclose()


def _status_error(status: int, text: str) -> BackendError:
    if status == 429:
        return BackendError("http_429", f"rate limited upstream: {text}", status)
    if status == 404 or ("not found" in text.lower() and "model" in text.lower()):
        return BackendError("unknown_model", f"unknown model: {text}", status)
    if status >= 500:
        return BackendError("http_5xx", f"HTTP {status}: {text}", status)
    return BackendError("http_4xx", f"HTTP {status}: {text}", status)
