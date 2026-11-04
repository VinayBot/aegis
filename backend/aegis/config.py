"""The gateway YAML: backends, model chains, API keys, cache and router tuning.

`ConfigStore.get()` re-reads the file when its mtime changes, so routing, limits and thresholds
can be edited while the gateway runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class BackendConfig(BaseModel):
    name: str = ""
    kind: Literal["openai", "litellm"] = "openai"
    label: str = ""
    base_url: str = ""
    api_base: str = ""
    model: str
    slots: int = 1
    process: str | None = None
    provider: str = "openai"
    api_key: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)


class Price(BaseModel):
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    reference: str = ""


class ModelConfig(BaseModel):
    name: str = ""
    chain: list[str]
    price: Price = Field(default_factory=Price)


class Budget(BaseModel):
    soft: float
    hard: float


class KeyConfig(BaseModel):
    tenant: str
    tpm: int
    budget: Budget
    # tenant: the data plane and read access to its own tenant's requests. admin: the control
    # plane too (chaos, cache, budgets, every tenant's data).
    role: Literal["tenant", "admin"] = "tenant"
    # Public identifier, safe to log, store and show. Raw keys never leave aegis.security.
    id: str = ""


class LimitsConfig(BaseModel):
    """Hard input bounds, checked before any guardrail, cache or model sees the request."""

    max_body_bytes: int = 1_000_000
    max_messages: int = 256
    max_prompt_tokens: int = 16_000
    max_output_tokens: int = 4096


class CacheConfig(BaseModel):
    ttl_seconds: int = 86400
    semantic_threshold: float = 0.95
    semantic_max_tokens: int = 200
    embed_budget_ms: int = 300


class RouterConfig(BaseModel):
    slot_wait_seconds: float = 2.0
    queue_deadline_seconds: float = 10.0
    first_token_timeout_seconds: float = 15.0
    hedge_min_ms: float = 250.0
    hedge_default_ms: float = 2500.0
    # When every backend in the chain fails before the first token, try the chain again after an
    # exponential backoff with full jitter (AWS style), never past the queue deadline.
    chain_retries: int = 1
    retry_base_ms: float = 200.0
    retry_cap_ms: float = 2000.0


class BreakerConfig(BaseModel):
    error_threshold: int = 3
    open_seconds: float = 30.0


class ChaosConfig(BaseModel):
    auto_revert_seconds: float = 120.0


class GatewayConfig(BaseModel):
    backends: dict[str, BackendConfig]
    models: dict[str, ModelConfig]
    keys: dict[str, KeyConfig]
    cache: CacheConfig = Field(default_factory=CacheConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    breaker: BreakerConfig = Field(default_factory=BreakerConfig)
    chaos: ChaosConfig = Field(default_factory=ChaosConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    def model_post_init(self, _: Any) -> None:
        for name, backend in self.backends.items():
            backend.name = name
            backend.label = backend.label or name
        for name, model in self.models.items():
            model.name = name
            unknown = [b for b in model.chain if b not in self.backends]
            if unknown:
                raise ValueError(f"model {name!r} chains unknown backend(s): {unknown}")
        seen: set[str] = set()
        for entry, key in self.keys.items():
            if not key.id:
                raw = (
                    entry[7:]
                    if entry.startswith("sha256:")
                    else hashlib.sha256(entry.encode()).hexdigest()
                )
                key.id = f"key_{raw[:10]}"
            if key.id in seen:
                raise ValueError(f"duplicate key id {key.id!r}")
            seen.add(key.id)

    def public_view(self) -> dict[str, Any]:
        """The config without secrets: keys appear by id, backend credentials are masked."""
        data = self.model_dump()
        data["keys"] = {k.id: {**k.model_dump(), "id": k.id} for k in self.keys.values()}
        for backend in data["backends"].values():
            if backend.get("api_key"):
                backend["api_key"] = "***"
        return data

    def effective_params(self, model: str, request: dict[str, Any]) -> dict[str, Any]:
        """The sampling parameters the request will actually run with: what the client sent,
        filled in with the first backend's real defaults. Part of the cache key, so a client that
        omits temperature is never served an answer generated at a different temperature."""
        chain = self.models[model].chain
        defaults = self.backends[chain[0]].defaults if chain else {}
        params: dict[str, Any] = {}
        for key in (
            "temperature",
            "top_p",
            "max_tokens",
            "stop",
            "seed",
            "presence_penalty",
            "frequency_penalty",
            # native tool calling and structured output pass through to the backend, and are part
            # of the cache key: the same question with different tools is a different request
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "response_format",
        ):
            value = request.get(key)
            if value is None:
                value = defaults.get(key)
            if value is not None:
                params[key] = value
        return params


def load_config(path: Path) -> GatewayConfig:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    return GatewayConfig.model_validate(data)


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime = path.stat().st_mtime
        self._config = load_config(path)
        self.reloads = 0
        self.last_error: str | None = None

    def get(self) -> GatewayConfig:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return self._config
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._config = load_config(self.path)
                self.reloads += 1
                self.last_error = None
            except Exception as exc:  # keep serving the last good config
                self.last_error = str(exc)
        return self._config

    def replace(self, config: GatewayConfig) -> None:
        """Tests and the control plane swap configuration in memory."""
        self._config = config
