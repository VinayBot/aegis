"""The control plane the website renders from: health, live topology, metrics, events, chaos,
playground, traces, docs and the AEGIS assistant."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aegis import architecture, assistant, scenarios, telemetry
from aegis.security import Principal, require_admin, require_viewer, websocket_principal
from aegis.state import AppState

# Access levels (see aegis/security.py): routes without a dependency are public (health, docs,
# the scenario list); VIEWER routes scope every row to the caller's tenant unless the caller is an
# admin; ADMIN routes see or change the whole gateway.
router = APIRouter(prefix="/api")
ws_router = APIRouter()
ADMIN = Depends(require_admin)


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.state
    return state


@router.get("/health", summary="Per-component status from real probes")
async def health(request: Request) -> dict[str, Any]:
    s = _state(request)
    components = s.health
    worst = "up"
    backends = s.config.get().backends
    for name, c in components.items():
        optional = name.startswith("backend:") and getattr(
            backends.get(name.split(":", 1)[1]), "optional", False
        )
        if (
            c["status"] == "down"
            and not optional
            and (name.startswith("backend:") or name in ("redis", "jaeger"))
        ):
            worst = "degraded"
    primary_ok = any(c["ok"] for n, c in components.items() if n.startswith("backend:"))
    if not primary_ok:
        worst = "down"
    return {
        "status": worst,
        "uptime_s": round(time.time() - s.started_at, 1),
        "redis_ok": s.redis_ok,
        "chaos_enabled": s.chaos.enabled,
        "env": s.settings.env,
        "trace_exporters": list(telemetry.exporters),
        "components": components,
    }


@router.get(
    "/architecture",
    dependencies=[ADMIN],
    summary="Live topology: nodes, edges, status, config, latency, counters",
)
async def get_architecture(request: Request) -> dict[str, Any]:
    return architecture.build(_state(request))


@router.get("/metrics", dependencies=[ADMIN], summary="Time series for the dashboard")
async def metrics(request: Request, window: int = 300, step: int = 5) -> dict[str, Any]:
    s = _state(request)
    window = max(30, min(window, 900))
    step = max(1, min(step, 60))
    out = s.metrics.series(window, step)
    out["kpis"] = s.metrics.kpis(60)
    out["breaker_history"] = list(s.metrics.breaker_history)[-200:]
    out["breakers"] = {
        n: {
            "state": rt.breaker.current_state(),
            "retry_after_s": round(rt.breaker.retry_after(), 1),
        }
        for n, rt in s.router.runtimes.items()
    }
    out["totals"] = await s.ledger.totals()
    out["tokens_per_min_by_key"] = s.metrics.tokens_per_min_by_key()
    out["limits"] = {v.id: {"tenant": v.tenant, "tpm": v.tpm} for v in s.config.get().keys.values()}
    out["server_time"] = time.time()
    return out


@router.get("/tenants", summary="Per-tenant usage this month with budgets")
async def tenants(
    request: Request, principal: Principal = Depends(require_viewer)
) -> list[dict[str, Any]]:
    s = _state(request)
    cfg = s.config.get()
    rows = {r["tenant"]: r for r in await s.ledger.tenants()}
    out = []
    for k in cfg.keys.values():
        if not principal.can_see(k.tenant):
            continue
        r = rows.get(k.tenant, {})
        out.append(
            {
                "tenant": k.tenant,
                "key_id": k.id,
                "tpm": k.tpm,
                "budget": k.budget.model_dump(),
                "spent_est_usd": round(await s.budgets.spent(k.tenant, s.redis_ok), 8),
                "requests": r.get("requests", 0),
                "tokens": r.get("tokens", 0),
                "rate_limited": r.get("rate_limited", 0) or 0,
                "saved_est_usd": r.get("saved_est_usd", 0.0),
                "cost_est_usd": r.get("cost_est_usd", 0.0),
                "feedback_up": r.get("feedback_up", 0),
                "feedback_down": r.get("feedback_down", 0),
            }
        )
    return out


@router.get("/requests", summary="Most recent requests (live feed)")
async def recent_requests(
    request: Request, limit: int = 50, principal: Principal = Depends(require_viewer)
) -> list[dict[str, Any]]:
    s = _state(request)
    limit = max(1, min(limit, 500))
    tenant = None if principal.is_admin else principal.tenant
    live = [r for r in s.metrics.recent_requests if tenant is None or r.get("tenant") == tenant]
    live = live[:limit]
    if len(live) >= limit:
        return live
    seen = {r["request_id"] for r in live}
    rows = await s.ledger.recent(limit, tenant=tenant)
    for row in rows:
        if row["id"] not in seen:
            live.append({**row, "request_id": row["id"]})
    return live[:limit]


@router.get("/requests/{request_id}", summary="One request: its ledger row and lifecycle events")
async def request_detail(
    request: Request, request_id: str, principal: Principal = Depends(require_viewer)
) -> dict[str, Any]:
    s = _state(request)
    tenant = None if principal.is_admin else principal.tenant
    row = await s.ledger.get(request_id, tenant=tenant)
    # lifecycle events carry no tenant; a tenant only sees them once its own row proves ownership
    if row is None and tenant is not None:
        raise HTTPException(404, "unknown request id")
    events = [e for e in s.bus.recent if e.get("request_id") == request_id]
    if row is None and not events:
        raise HTTPException(404, "unknown request id")
    return {
        "request": row,
        "events": events,
        "jaeger_url": f"{s.settings.jaeger_ui_url}/trace/{row['trace_id']}" if row else None,
    }


@router.get(
    "/events/recent",
    dependencies=[ADMIN],
    summary="Recent lifecycle events (also used by the replay recorder)",
)
async def recent_events(request: Request, limit: int = 300) -> list[dict[str, Any]]:
    return list(_state(request).bus.recent)[-limit:]


# ------------------------------------------------------------------------------ chaos


class ChaosAction(BaseModel):
    backend: str
    action: str = Field(description="latency | error_rate | kill")
    value: float | bool | None = None
    revert_after_s: float | None = None


@router.get("/chaos", dependencies=[ADMIN], summary="Chaos state and action log")
async def chaos_state(request: Request) -> dict[str, Any]:
    return _state(request).chaos.snapshot()


@router.post(
    "/chaos",
    dependencies=[ADMIN],
    summary="Inject latency or errors, or kill/revive a backend (dev only)",
)
async def chaos_apply(request: Request, action: ChaosAction) -> dict[str, Any]:
    s = _state(request)
    cfg = s.config.get()
    if action.backend not in cfg.backends:
        raise HTTPException(404, f"unknown backend {action.backend}")
    try:
        entry = s.chaos.apply(
            action.backend,
            action.action,
            action.value,
            process=cfg.backends[action.backend].process,
            revert_after=action.revert_after_s,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"applied": entry, "state": s.chaos.snapshot()}


@router.post("/chaos/revert", dependencies=[ADMIN], summary="Revert every active fault")
async def chaos_revert(request: Request) -> dict[str, Any]:
    s = _state(request)
    if not s.chaos.enabled:
        raise HTTPException(403, "chaos is disabled")
    s.chaos.revert_all()
    return s.chaos.snapshot()


@router.post(
    "/breakers/{backend}/reset", dependencies=[ADMIN], summary="Close a breaker by hand (dev only)"
)
async def breaker_reset(request: Request, backend: str) -> dict[str, Any]:
    s = _state(request)
    if not s.chaos.enabled:
        raise HTTPException(403, "chaos is disabled")
    rt = s.router.runtimes.get(backend)
    if rt is None:
        raise HTTPException(404, "unknown backend")
    rt.breaker.force("closed", "manual reset")
    return {"backend": backend, "state": rt.breaker.current_state()}


@router.get("/scenarios", summary="Guided Chaos Lab scenarios")
async def scenario_list(request: Request) -> list[dict[str, Any]]:
    runner: scenarios.ScenarioRunner = request.app.state.scenarios
    return runner.list()


@router.post(
    "/scenarios/{name}",
    dependencies=[ADMIN],
    summary="Start a guided scenario; progress streams as events",
)
async def scenario_start(request: Request, name: str) -> dict[str, Any]:
    s = _state(request)
    if not s.chaos.enabled and name in ("primary_crash", "slow_primary"):
        raise HTTPException(403, "this scenario injects faults: chaos is disabled")
    try:
        run_id = request.app.state.scenarios.start(name)
    except KeyError as exc:
        raise HTTPException(404, f"unknown scenario {name}") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id}


# ------------------------------------------------------------------------------ playground


class LoadTest(BaseModel):
    n: int = Field(10, ge=1, le=40)
    prompt: str | None = None
    distinct: bool = True
    # duplicate only: the Playground's own settings (a browser cannot open ten streams at once,
    # it allows six per host, so its "10x at once" is fired from here)
    model: str = "aegis-chat"
    key: str | None = None
    temperature: float | None = 0.0
    no_cache: bool | None = None


@router.post(
    "/playground/burst",
    dependencies=[ADMIN],
    summary="N concurrent requests (distinct prompts by default)",
)
async def pg_burst(request: Request, body: LoadTest) -> dict[str, Any]:
    results = await scenarios.burst(_state(request), body.n, body.distinct, body.prompt)
    return {"results": results}


@router.post(
    "/playground/duplicate",
    dependencies=[ADMIN],
    summary="N identical concurrent requests (coalescing test)",
)
async def pg_duplicate(request: Request, body: LoadTest) -> dict[str, Any]:
    return {
        "results": await scenarios.duplicate(
            _state(request),
            body.n,
            body.prompt,
            model=body.model,
            key=body.key,
            temperature=body.temperature,
            no_cache=body.no_cache,
        )
    }


@router.post(
    "/playground/flood",
    dependencies=[ADMIN],
    summary="Rapid requests on the low-limit key (429 test)",
)
async def pg_flood(request: Request, body: LoadTest) -> dict[str, Any]:
    return {"results": await scenarios.flood(_state(request), min(body.n, 20))}


@router.post(
    "/selfcheck",
    dependencies=[ADMIN],
    summary="End-to-end probe: embed, cache lookup, 1-token generation",
)
async def selfcheck(request: Request) -> dict[str, Any]:
    s = _state(request)
    out: dict[str, Any] = {"ts": time.time(), "stages": []}
    t = time.perf_counter()
    try:
        await asyncio.wait_for(s.embedder.embed_one("aegis self-check"), 2.0)
        out["stages"].append({"stage": "embedder", "ok": True, "ms": _ms(t)})
    except Exception as exc:
        out["stages"].append({"stage": "embedder", "ok": False, "ms": _ms(t), "error": str(exc)})
    t = time.perf_counter()
    try:
        await s.cache.get_exact("selfcheck-" + str(time.time()))
        out["stages"].append({"stage": "exact_cache", "ok": True, "ms": _ms(t)})
    except Exception as exc:
        out["stages"].append({"stage": "exact_cache", "ok": False, "ms": _ms(t), "error": str(exc)})
    r = await scenarios.fire(
        s, "Reply with OK.", no_cache=True, max_tokens=1, client_name="selfcheck"
    )
    summary = r.get("summary") or {}
    out["generation"] = {
        "ok": r.get("status") == 200,
        "status": r.get("status"),
        "backend": r.get("backend"),
        "ms": r.get("ms"),
        "request_id": r.get("request_id"),
        "error": r.get("error"),
    }
    out["stages"] += [
        {
            "stage": st["stage"],
            "ok": st["outcome"] not in ("error", "rejected"),
            "ms": st["ms"],
            "outcome": st["outcome"],
        }
        for st in summary.get("stages", [])
    ]
    out["ok"] = all(st["ok"] for st in out["stages"]) and out["generation"]["ok"]
    return out


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 2)


# ------------------------------------------------------------------------------ config & caches


class Tune(BaseModel):
    semantic_threshold: float | None = Field(None, ge=0.5, le=1.0)


@router.get("/config", dependencies=[ADMIN], summary="The effective gateway configuration")
async def config(request: Request) -> dict[str, Any]:
    s = _state(request)
    return {
        "config": s.config.get().public_view(),
        "path": str(s.config.path),
        "reloads": s.config.reloads,
        "error": s.config.last_error,
    }


@router.post(
    "/cache/tune", dependencies=[ADMIN], summary="Adjust the semantic threshold at runtime"
)
async def cache_tune(request: Request, body: Tune) -> dict[str, Any]:
    s = _state(request)
    cfg = s.config.get()
    if body.semantic_threshold is not None:
        cfg.cache.semantic_threshold = body.semantic_threshold
        s.bus.publish(
            "config.change", None, field="cache.semantic_threshold", value=body.semantic_threshold
        )
    return {"cache": cfg.cache.model_dump()}


@router.get("/cache/stats", dependencies=[ADMIN], summary="Cache sizes")
async def cache_stats(request: Request) -> dict[str, Any]:
    return await _state(request).cache.stats()


@router.post("/cache/flush", dependencies=[ADMIN], summary="Empty both caches (dev)")
async def cache_flush(request: Request) -> dict[str, Any]:
    s = _state(request)
    removed = await s.cache.flush()
    s.bus.publish("cache.flush", None, removed=removed)
    return {"removed": removed}


@router.post(
    "/budgets/{tenant}/reset", dependencies=[ADMIN], summary="Reset a tenant's monthly spend (dev)"
)
async def budget_reset(request: Request, tenant: str) -> dict[str, Any]:
    await _state(request).budgets.reset(tenant)
    return {"tenant": tenant, "spent": 0}


# ------------------------------------------------------------------------------ traces


def _span_view(span: dict[str, Any], trace_start: int) -> dict[str, Any]:
    tags = {t["key"]: t.get("value") for t in span.get("tags", [])}
    parent = next(
        (r["spanID"] for r in span.get("references", []) if r.get("refType") == "CHILD_OF"), None
    )
    return {
        "span_id": span["spanID"],
        "parent_id": parent,
        "name": span["operationName"],
        "start_ms": round((span["startTime"] - trace_start) / 1000, 2),
        "duration_ms": round(span["duration"] / 1000, 2),
        "error": tags.get("otel.status_code") == "ERROR" or tags.get("error") is True,
        "tags": tags,
    }


def _trace_view(trace: dict[str, Any]) -> dict[str, Any]:
    spans = trace.get("spans", [])
    if not spans:
        return {"trace_id": trace.get("traceID"), "spans": []}
    start = min(s["startTime"] for s in spans)
    end = max(s["startTime"] + s["duration"] for s in spans)
    views = sorted((_span_view(s, start) for s in spans), key=lambda v: v["start_ms"])
    root = next((v for v in views if v["parent_id"] is None), views[0])
    tags = root["tags"]
    return {
        "trace_id": trace["traceID"],
        "start": start / 1e6,
        "duration_ms": round((end - start) / 1000, 2),
        "root": root["name"],
        "request_id": tags.get("aegis.request_id"),
        "status": tags.get("aegis.status"),
        "cache": tags.get("aegis.cache"),
        "backend": tags.get("aegis.backend"),
        "tenant": tags.get("aegis.tenant"),
        "hedged": tags.get("aegis.hedged"),
        "failovers": tags.get("aegis.failovers"),
        "span_count": len(views),
        "spans": views,
    }


@router.get("/traces", dependencies=[ADMIN], summary="Recent traces from Jaeger's query API")
async def traces(
    request: Request, limit: int = 30, outcome: str | None = None, lookback: str = "1h"
) -> dict[str, Any]:
    s = _state(request)
    params: dict[str, Any] = {
        "service": s.settings.service_name,
        "limit": min(limit, 100),
        "lookback": lookback,
    }
    if outcome:
        params["tags"] = json.dumps(
            {"aegis.status": outcome}
            if outcome in ("ok", "rejected", "blocked", "error", "midstream_error")
            else {"aegis.cache": outcome}
        )
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{s.settings.jaeger_query_url}/api/traces", params=params)
        data = resp.json().get("data") or []
    except Exception as exc:
        raise HTTPException(503, f"Jaeger query API unreachable: {type(exc).__name__}") from exc
    views = sorted((_trace_view(t) for t in data), key=lambda v: v.get("start", 0), reverse=True)
    return {"traces": views, "jaeger_ui": s.settings.jaeger_ui_url}


@router.get("/traces/{trace_id}", dependencies=[ADMIN], summary="One trace as a waterfall")
async def trace(request: Request, trace_id: str) -> dict[str, Any]:
    s = _state(request)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{s.settings.jaeger_query_url}/api/traces/{trace_id}")
        data = resp.json().get("data") or []
    except Exception as exc:
        raise HTTPException(503, f"Jaeger query API unreachable: {type(exc).__name__}") from exc
    if not data:
        raise HTTPException(404, "trace not found (it may still be in the export batch)")
    return {**_trace_view(data[0]), "jaeger_url": f"{s.settings.jaeger_ui_url}/trace/{trace_id}"}


# ------------------------------------------------------------------------------ docs + assistant

DOC_TITLES = {
    "README.md": "Overview",
    "ARCHITECTURE.md": "Architecture",
    "DECISIONS.md": "Decisions",
    "API.md": "API reference",
    "DEMO_SCRIPT.md": "Demo script",
    "DESIGN.md": "Design system",
    "SKILLS_REVIEW.md": "Skills review",
    "VALIDATION_REPORT.md": "Validation report",
    "PLAN.md": "Plan",
    "PRODUCTION_READINESS.md": "Production readiness",
    "PEER_AUDIT.md": "Peer audit",
    "LEARNING.md": "Learning guide",
    "SECURITY.md": "Security",
    "CHANGELOG.md": "Changelog",
    "CONTRIBUTING.md": "Contributing",
}
ROOT_DOCS = (
    "README.md",
    "LEARNING.md",
    "DESIGN.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
)


@router.get("/docs", summary="Project documents")
async def docs(request: Request) -> list[dict[str, Any]]:
    s = _state(request)
    root = s.settings.project_dir
    out = []
    for path in [*(root / n for n in ROOT_DOCS), *sorted(s.settings.docs_dir.glob("*.md"))]:
        if path.exists():
            out.append(
                {
                    "slug": path.name,
                    "title": DOC_TITLES.get(path.name, path.stem.title()),
                    "path": str(path.relative_to(root)),
                    "bytes": path.stat().st_size,
                }
            )
    return out


@router.get("/docs/{slug}", summary="One project document (markdown)")
async def doc(request: Request, slug: str) -> dict[str, Any]:
    s = _state(request)
    root = s.settings.project_dir
    for path in (root / slug, s.settings.docs_dir / slug):
        if (
            path.name == slug
            and path.suffix == ".md"
            and path.exists()
            and path.parent in (root, s.settings.docs_dir)
        ):
            return {
                "slug": slug,
                "title": DOC_TITLES.get(slug, path.stem),
                "markdown": path.read_text(),
            }
    raise HTTPException(404, "unknown document")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    no_cache: bool = False


@router.post(
    "/assistant/chat",
    dependencies=[ADMIN],  # its live context spans every tenant
    summary="Ask AEGIS about the project (SSE); routed through the gateway",
)
async def assistant_chat(request: Request, body: AskBody) -> StreamingResponse:
    s = _state(request)

    async def gen() -> Any:
        try:
            async for item in assistant.answer(s, body.question, body.history, body.no_cache):
                yield f"data: {json.dumps(item)}\n\n".encode()
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'status': 500, 'error': {'message': str(exc)}})}\n\n".encode()
        yield b'data: {"type":"done"}\n\n'

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"cache-control": "no-store"}
    )


# ------------------------------------------------------------------------------ websocket


@ws_router.websocket("/ws/events")
async def events_ws(ws: WebSocket) -> None:
    """Every lifecycle event, live. Three tasks race: send events, send heartbeats, and receive.
    Whichever ends first (normally the receiver seeing the client or the server close the
    socket) cancels the others, so a closed socket is noticed at once, even when no event is
    flowing, and a shutting-down server is never held open by an idle dashboard."""
    if websocket_principal(ws) is None:
        await ws.close(code=1008, reason="admin key required")  # policy violation
        return
    await ws.accept()
    s: AppState = ws.app.state.state
    async with s.bus.subscribe() as queue:
        await ws.send_json(
            {"type": "hello", "server_time": time.time(), "recent": list(s.bus.recent)[-150:]}
        )

        async def send_events() -> None:
            while True:
                await ws.send_json(await queue.get())

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(3)
                await ws.send_json({"type": "heartbeat", "ts": time.time()})

        async def receive() -> None:
            while True:  # clients send nothing meaningful; this only watches for the close
                if (await ws.receive())["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.ensure_future(t()) for t in (send_events, heartbeat, receive)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                # a send on the closed socket ends with an error; that is the expected way out
                with contextlib.suppress(
                    asyncio.CancelledError, WebSocketDisconnect, RuntimeError, Exception
                ):
                    await t
