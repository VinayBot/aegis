"""FastAPI app: builds the gateway's state at startup and tears it down cleanly.

uv run uvicorn aegis.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from aegis import __version__, guardrails, telemetry
from aegis.api import control, gateway
from aegis.assistant import DocsIndex
from aegis.budget import Budgets
from aegis.cache import ResponseCache
from aegis.chaos import ChaosController
from aegis.coalesce import Coalescer
from aegis.config import ConfigStore
from aegis.embedder import Embedder
from aegis.events import EventBus
from aegis.health import HealthMonitor
from aegis.ledger import Ledger
from aegis.metrics import Metrics
from aegis.pipeline import Pipeline
from aegis.ratelimit import RateLimiter
from aegis.router import Router
from aegis.scenarios import ScenarioRunner
from aegis.settings import Settings, get_settings
from aegis.state import AppState, Recorder, build_backend

log = logging.getLogger("aegis")


async def _record_system_events(state: AppState) -> None:
    """Breaker trips and chaos actions are durable facts: keep them in the ledger."""
    async with state.bus.subscribe() as queue:
        while True:
            e = await queue.get()
            if e["type"] == "breaker.state":
                kind = "breaker_open" if e["state"] == "open" else f"breaker_{e['state']}"
                state.ledger.record_event(kind, e["backend"], {"reason": e.get("reason")})
            elif e["type"] == "chaos.action":
                state.ledger.record_event(
                    "chaos", e.get("backend"), {"action": e.get("action"), "value": e.get("value")}
                )


def build_state(settings: Settings) -> AppState:
    config = ConfigStore(settings.config_path)
    cfg = config.get()
    bus = EventBus()
    redis = Redis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
    chaos = ChaosController(
        bus, settings.run_dir, settings.chaos_enabled, cfg.chaos.auto_revert_seconds
    )
    router = Router(bus, wrap=chaos.wrap, build=build_backend)
    router.sync(cfg)
    return AppState(
        settings=settings,
        config=config,
        bus=bus,
        redis=redis,
        cache=ResponseCache(redis, settings.redis_prefix),
        limiter=RateLimiter(redis, settings.redis_prefix),
        budgets=Budgets(redis, settings.redis_prefix),
        embedder=Embedder(settings.embedder_model),
        coalescer=Coalescer(),
        router=router,
        chaos=chaos,
        ledger=Ledger(settings.ledger_path),
        metrics=Metrics(bus),
        recorder=Recorder(),
        started_at=time.time(),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)  # probes run twice a second
        logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
        telemetry.setup_tracing(
            settings.service_name, settings.otlp_endpoint if settings.tracing_enabled else None
        )
        state = build_state(settings)
        app.state.state = state
        app.state.pipeline = Pipeline(state)
        app.state.scenarios = ScenarioRunner(state)
        with contextlib.suppress(Exception):
            await state.redis.ping()  # type: ignore[union-attr]
            await state.cache.ensure_index()
        await asyncio.gather(
            asyncio.to_thread(state.embedder.load), asyncio.to_thread(guardrails.warm)
        )
        state.ledger.start()
        state.metrics.start()
        state.recorder.start()
        monitor = HealthMonitor(state)
        await monitor.check_all()
        monitor.start()
        system_events = asyncio.get_running_loop().create_task(_record_system_events(state))
        docs = DocsIndex(state)
        state.docs_index = docs  # type: ignore[attr-defined]
        await asyncio.to_thread(docs.build, app.openapi())
        if settings.chaos_enabled:
            log.warning("CHAOS ENABLED: fault injection endpoints are live (dev only)")
        log.info(
            "AEGIS %s ready: %d backends, %d doc chunks",
            __version__,
            len(state.router.runtimes),
            len(docs.chunks),
        )
        try:
            yield
        finally:
            await state.chaos.shutdown()
            system_events.cancel()
            await monitor.stop()
            await state.metrics.stop()
            await state.recorder.stop()
            await state.ledger.stop()
            await state.router.aclose()
            if state.redis is not None:
                await state.redis.aclose()
            state.ledger.close()
            telemetry.shutdown_tracing()

    app = FastAPI(
        title="AEGIS AI Gateway",
        version=__version__,
        lifespan=lifespan,
        description="OpenAI-compatible AI gateway with semantic caching, coalescing, "
        "hedging, failover, rate limits, budgets and live observability.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "x-cache",
            "x-aegis-request-id",
            "x-aegis-trace-id",
            "x-aegis-backend",
            "x-aegis-hedged",
            "x-aegis-failovers",
            "x-aegis-budget-warning",
            "x-aegis-similarity",
            "x-aegis-coalesced-with",
            "retry-after",
            "x-aegis-cost-label",
        ],
    )
    app.include_router(gateway.router)
    app.include_router(control.router)
    app.include_router(control.ws_router)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            {"error": {"message": f"internal error: {type(exc).__name__}", "code": "internal"}}, 500
        )

    dist = Path(settings.project_dir) / "frontend" / "dist"
    if dist.exists():  # `make serve`: the built site from the same origin
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            candidate = dist / path
            if path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
