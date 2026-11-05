"""Shared fixtures: real Redis (from `make infra`), fake model backends served over real HTTP by
uvicorn on random ports, and a full gateway app built from a test YAML.

Every test run uses its own Redis key prefix, so tests never see the dev gateway's cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis.asyncio import Redis

from aegis import telemetry
from aegis.settings import Settings
from tests.fake_backend import FakeControl, build_app

REDIS_URL = "redis://127.0.0.1:6379/0"
SPANS = InMemorySpanExporter()
telemetry.setup_tracing("aegis-test", None, exporter=SPANS)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def serve(app: Any, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.get_running_loop().create_task(server.serve())
    # startup loads the embedder and builds the docs index: seconds on a laptop, longer on a
    # shared CI runner, so wait up to a minute and say so if it never comes up
    for _ in range(3000):
        if server.started or task.done():
            break
        await asyncio.sleep(0.02)
    if not server.started:
        exc = task.exception() if task.done() else None
        raise RuntimeError(f"test server on port {port} did not start: {exc!r}")
    return server, task


async def stop(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, 5)


async def redis_available() -> bool:
    r = Redis.from_url(REDIS_URL, socket_connect_timeout=0.5)
    try:
        await r.ping()
        return True
    except Exception:
        return False
    finally:
        await r.aclose()


@pytest.fixture(scope="session")
async def redis_up() -> bool:
    return await redis_available()


@pytest.fixture
async def require_redis(redis_up: bool) -> None:
    if not redis_up:
        pytest.skip("Redis Stack is not running (make infra)")


@pytest.fixture
async def fakes() -> AsyncIterator[dict[str, FakeControl]]:
    """Two fake backends: `primary` and `fallback`, each with its own control."""
    controls = {
        "primary": FakeControl(model="fake-primary"),
        "fallback": FakeControl(model="fake-fallback"),
    }
    servers = []
    for _name, control in controls.items():
        port = free_port()
        control.port = port  # type: ignore[attr-defined]
        servers.append(await serve(build_app(control), port))
    yield controls
    for server, task in servers:
        await stop(server, task)


def write_config(tmp: Path, ports: dict[str, int], **overrides: Any) -> Path:
    cfg: dict[str, Any] = {
        "backends": {
            "primary": {
                "kind": "openai",
                "base_url": f"http://127.0.0.1:{ports['primary']}/v1",
                "model": "fake-primary",
                "slots": 1,
                "provider": "fake",
                "defaults": {"temperature": 0.0, "max_tokens": 64},
            },
            "fallback": {
                "kind": "openai",
                "base_url": f"http://127.0.0.1:{ports['fallback']}/v1",
                "model": "fake-fallback",
                "slots": 2,
                "provider": "fake",
                "defaults": {"temperature": 0.8, "max_tokens": 64},
            },
        },
        "models": {
            "aegis-chat": {
                "chain": ["primary", "fallback"],
                "price": {"input_per_1m": 1.0, "output_per_1m": 2.0, "reference": "test"},
            }
        },
        "keys": {
            "k-demo": {"tenant": "demo", "tpm": 100000, "budget": {"soft": 10, "hard": 20}},
            # the key the built-in scenarios and Playground load tests send with
            "aegis-demo-key": {"tenant": "demo", "tpm": 100000, "budget": {"soft": 10, "hard": 20}},
            "k-small": {"tenant": "small", "tpm": 200, "budget": {"soft": 10, "hard": 20}},
            "k-broke": {"tenant": "broke", "tpm": 100000, "budget": {"soft": 0.0, "hard": 0.0}},
            "k-other": {"tenant": "other", "tpm": 100000, "budget": {"soft": 10, "hard": 20}},
            # the key the AEGIS assistant asks the gateway with
            "aegis-assistant-key": {
                "tenant": "aegis-bot",
                "tpm": 100000,
                "budget": {"soft": 10, "hard": 20},
            },
            # the key the built-in rate-limit flood sends with
            "aegis-flood-key": {"tenant": "flood", "tpm": 300, "budget": {"soft": 10, "hard": 20}},
            "k-admin": {
                "id": "ops",
                "tenant": "ops",
                "tpm": 100000,
                "budget": {"soft": 10, "hard": 20},
                "role": "admin",
            },
        },
        "cache": {
            "ttl_seconds": 60,
            "semantic_threshold": 0.95,
            "semantic_max_tokens": 200,
            "embed_budget_ms": 2000,
        },
        "router": {
            "slot_wait_seconds": 0.3,
            "queue_deadline_seconds": 2,
            "first_token_timeout_seconds": 1.5,
            "hedge_min_ms": 150,
            "hedge_default_ms": 400,
            # off by default so breaker tests count exact errors; the retry tests turn it on
            "chain_retries": 0,
            "retry_base_ms": 50,
            "retry_cap_ms": 200,
        },
        "breaker": {"error_threshold": 3, "open_seconds": 30},
        "chaos": {"auto_revert_seconds": 30},
        "limits": {},
        "safety": {"enabled": False},
        "output_guard": {"redact": True},
    }
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        cfg[section][field] = value
    path = tmp / "gateway.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


class Gateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        app: Any,
        url: str,
        spans: InMemorySpanExporter | None = None,
    ) -> None:
        self.client = client
        self.app = app
        self.url = url
        self.spans = spans or InMemorySpanExporter()

    @property
    def state(self) -> Any:
        return self.app.state.state

    async def chat(
        self,
        prompt: str = "ping",
        key: str = "k-demo",
        stream: bool = True,
        headers: dict[str, str] | None = None,
        **body: Any,
    ) -> httpx.Response:
        payload = {
            "model": "aegis-chat",
            "stream": stream,
            "messages": [{"role": "user", "content": prompt}],
            **body,
        }
        return await self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"authorization": f"Bearer {key}", "x-aegis-summary": "1", **(headers or {})},
        )


def sse_summary(text: str) -> dict[str, Any]:
    import json

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == "event: aegis":
            return dict(json.loads(lines[i + 1][5:]))
    raise AssertionError(f"no aegis summary in stream: {text[:300]}")


@pytest.fixture
async def gateway(
    tmp_path: Path,
    fakes: dict[str, FakeControl],
    require_redis: None,
    request: pytest.FixtureRequest,
) -> AsyncIterator[Gateway]:
    from aegis.main import create_app

    overrides = dict(getattr(request, "param", {}))
    ports = {n: c.port for n, c in fakes.items()}  # type: ignore[attr-defined]
    port = free_port()
    settings = Settings(
        env=overrides.pop("env", "dev"),  # the suite acts as the local operator unless a test says
        admin_key=overrides.pop("admin_key", None),
        # the test keys are plaintext; production's refusal of them has its own test
        allow_plaintext_keys=overrides.pop("allow_plaintext_keys", True),
        jwt_secret=overrides.pop("jwt_secret", "test-jwt-secret-0123456789abcdef0123"),
        config_path=write_config(tmp_path, ports, **overrides),
        redis_url=REDIS_URL,
        redis_prefix=f"aegistest-{uuid.uuid4().hex[:8]}",
        ledger_path=tmp_path / "ledger.sqlite3",
        run_dir=tmp_path / "run",
        docs_dir=tmp_path / "docs",
        project_dir=tmp_path,
        self_url=f"http://127.0.0.1:{port}",
        chaos_enabled=True,
        tracing_enabled=False,
        health_interval_seconds=0.2,
    )
    app = create_app(settings)
    server, task = await serve(app, port)
    spans = InMemorySpanExporter()  # fresh per test: a shut-down exporter drops everything
    telemetry.setup_tracing("aegis-test", None, exporter=spans)  # lifespan reset it
    async with httpx.AsyncClient(base_url=settings.self_url, timeout=20) as client:
        gw = Gateway(client, app, settings.self_url, spans)
        gw.server, gw.task = server, task  # type: ignore[attr-defined]
        yield gw
    state = app.state.state
    with contextlib.suppress(Exception):
        await state.cache.flush()
        r = Redis.from_url(REDIS_URL)
        async for key in r.scan_iter(match=f"{settings.redis_prefix}:*"):
            await r.delete(key)
        with contextlib.suppress(Exception):
            await r.execute_command("FT.DROPINDEX", f"{settings.redis_prefix}:semidx")
        await r.aclose()
    await stop(server, task)
