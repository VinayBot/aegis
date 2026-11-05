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

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from aegis import __version__, guardrails, logcontext, security, telemetry
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
from aegis.prom import CONTENT_TYPE_LATEST, PromMetrics
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


def _harden(settings: Settings) -> Settings:
    """Production refuses dev-only behaviour even if it was switched on by mistake."""
    if not settings.is_dev and settings.chaos_enabled:
        log.error("AEGIS_CHAOS_ENABLED ignored: fault injection is dev-only (AEGIS_ENV=dev)")
        settings = settings.model_copy(update={"chaos_enabled": False})
    return settings


def _check_keys(settings: Settings, state: AppState) -> None:
    cfg = state.config.get()
    plaintext = security.plaintext_keys(cfg)
    if plaintext and not security.plaintext_allowed(settings):
        log.error(
            "plaintext API keys in the gateway YAML are ignored in production; store them "
            "as sha256: entries (or set AEGIS_ALLOW_PLAINTEXT_KEYS=1 for a local demo)",
            extra={"ignored_key_ids": plaintext},
        )
    # ledgers from before key ids hold raw keys: replace them with the public ids
    state.ledger.remap_key_ids(
        {entry: k.id for entry, k in cfg.keys.items() if not entry.startswith("sha256:")}
    )


def _warm_safety(state: AppState) -> None:
    """Load Llama Guard now, so the first request is not a cold start."""
    sc = state.config.get().safety
    if sc.enabled:
        warm = state.safety.classify(sc.base_url, sc.model, [{"role": "user", "content": "hi"}], 10)
        state.safety_warmup = asyncio.get_running_loop().create_task(warm)  # type: ignore[attr-defined]


def _announce(settings: Settings, state: AppState, doc_chunks: int) -> None:
    if settings.chaos_enabled:
        log.warning("CHAOS ENABLED: fault injection endpoints are live (dev only)")
    if settings.is_dev:
        log.warning("AEGIS_ENV=dev: loopback callers act as the local operator (admin)")
    log.info(
        "AEGIS %s ready: %d backends, %d doc chunks",
        __version__,
        len(state.router.runtimes),
        doc_chunks,
    )


def _add_ops_routes(app: FastAPI) -> None:
    """Liveness, readiness and Prometheus."""

    @app.get("/healthz", summary="Liveness: the process and its event loop respond", tags=["ops"])
    async def healthz() -> dict[str, object]:
        return {"status": "alive", "version": __version__}

    @app.get(
        "/readyz",
        summary="Readiness: config loaded and at least one backend can serve",
        tags=["ops"],
        responses={503: {"description": "not ready: no backend can serve"}},
    )
    async def readyz(request: Request) -> JSONResponse:
        state: AppState = request.app.state.state
        backends = {
            n.split(":", 1)[1]: c["ok"] for n, c in state.health.items() if n.startswith("backend:")
        }
        usable = [
            n
            for n, ok in backends.items()
            if ok
            and n in state.router.runtimes
            and state.router.runtimes[n].breaker.current_state() != "open"
        ]
        # a bad hot-reload is reported but does not fail readiness: the last good config serves
        ready = bool(usable)
        body = {
            "status": "ready" if ready else "not_ready",
            "usable_backends": usable,
            "redis": "ok" if state.redis_ok else "degraded (caches skipped, local rate limits)",
            "config_error": state.config.last_error,
        }
        return JSONResponse(body, 200 if ready else 503)

    @app.get(
        "/metrics",
        summary="Prometheus metrics (text exposition format)",
        tags=["ops"],
        dependencies=[Depends(security.require_admin)],
        response_class=Response,
    )
    async def prometheus(request: Request) -> Response:
        state: AppState = request.app.state.state
        body = state.prom.render(state.router.runtimes)  # type: ignore[attr-defined]
        return Response(body, media_type=CONTENT_TYPE_LATEST)


def _mount_site(app: FastAPI, settings: Settings) -> None:
    """`make serve` and the container: the built site from the same origin."""
    dist = Path(settings.project_dir) / "frontend" / "dist"
    if not dist.exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    root = dist.resolve()
    index = root / "index.html"
    site_files = {
        f.relative_to(root).as_posix(): f
        for f in root.rglob("*")
        if f.is_file() and not f.is_symlink()
    }

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        # the request only selects from files listed at startup; it never builds a path, so
        # "../" or a symlink cannot reach anything outside the built site
        target = site_files.get(path)
        return FileResponse(target if target is not None else index)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = _harden(settings or get_settings())

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logcontext.setup(settings.log_format, settings.log_level)
        telemetry.setup_tracing(
            settings.service_name,
            settings.otlp_endpoint if settings.tracing_enabled else None,
            langfuse=telemetry.langfuse_target(settings) if settings.tracing_enabled else None,
        )
        state = build_state(settings)
        _check_keys(settings, state)
        prom = PromMetrics(state.bus)
        state.prom = prom  # type: ignore[attr-defined]
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
        prom.start()
        state.recorder.start()
        monitor = HealthMonitor(state)
        state.health_monitor = monitor  # type: ignore[attr-defined]
        await monitor.check_all()
        monitor.start()
        system_events = asyncio.get_running_loop().create_task(_record_system_events(state))
        _warm_safety(state)
        docs = DocsIndex(state)
        state.docs_index = docs  # type: ignore[attr-defined]
        await asyncio.to_thread(docs.build, app.openapi())
        _announce(settings, state, len(docs.chunks))
        try:
            yield
        finally:
            await state.chaos.shutdown()
            system_events.cancel()
            await monitor.stop()
            await state.metrics.stop()
            await prom.stop()
            await state.recorder.stop()
            await state.ledger.stop()
            await state.router.aclose()
            await state.safety.aclose()
            if state.redis is not None:
                await state.redis.aclose()
            state.ledger.close()
            telemetry.shutdown_tracing()

    app = FastAPI(
        title="AEGIS AI Gateway",
        version=__version__,
        lifespan=lifespan,
        # /docs belongs to the site (it serves the built frontend); the API explorer lives under /api
        docs_url="/api/swagger",
        redoc_url=None,
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

    _add_ops_routes(app)  # before the site's catch-all route, which must not shadow them

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            {"error": {"message": f"internal error: {type(exc).__name__}", "code": "internal"}}, 500
        )

    _mount_site(app, settings)
    return app


app = create_app()
