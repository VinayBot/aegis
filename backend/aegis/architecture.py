"""The live topology served at GET /api/architecture. The website draws the graph from this; a
backend added to the YAML appears as a node with no frontend change.
"""

from __future__ import annotations

import itertools
from typing import Any

from aegis.config import GatewayConfig
from aegis.state import AppState

SOURCE = "backend/aegis"

# id, label, overview group, kind, source file, one plain-English sentence
PIPELINE: list[tuple[str, str, str, str, str, str]] = [
    (
        "client",
        "Client",
        "Ingress",
        "client",
        "api/gateway.py",
        "Any OpenAI-compatible client: the playground, the AEGIS assistant, Veritas, NEXUS.",
    ),
    (
        "auth",
        "Auth",
        "Ingress",
        "stage",
        "pipeline.py",
        "Checks the API key and finds the tenant it belongs to.",
    ),
    (
        "guardrails",
        "Guardrails",
        "Ingress",
        "stage",
        "guardrails/__init__.py",
        "Redacts personal data and blocks prompt-injection attempts before anything else sees the prompt.",
    ),
    (
        "ratelimit",
        "Rate limiter",
        "Policy",
        "stage",
        "ratelimit.py",
        "Token bucket per key in Redis: tokens per minute, 429 with Retry-After when exceeded.",
    ),
    (
        "budget",
        "Budget",
        "Policy",
        "stage",
        "budget.py",
        "Monthly spend per tenant in est. cloud-equivalent dollars: warns at the soft limit, blocks at the hard one.",
    ),
    (
        "exact_cache",
        "Exact cache",
        "Memory",
        "stage",
        "cache.py",
        "Same tenant, model, messages and parameters as before: replay the stored answer instantly.",
    ),
    (
        "semantic_cache",
        "Semantic cache",
        "Memory",
        "stage",
        "cache.py",
        "A paraphrase of an earlier question in the same conversation: replay its answer if cosine >= threshold.",
    ),
    (
        "embedder",
        "Embedder",
        "Memory",
        "side",
        "embedder.py",
        "bge-small on the CPU inside the gateway, so a model-server crash cannot take out the cache.",
    ),
    (
        "coalescer",
        "Coalescer",
        "Memory",
        "stage",
        "coalesce.py",
        "Identical requests in flight at once share one upstream call.",
    ),
    (
        "safety",
        "Content safety",
        "Reflexes",
        "stage",
        "guardrails/safety.py",
        "Llama Guard 3 checks the prompt on a cache miss (and the answer before it is cached); "
        "blocks only when P(unsafe) >= the threshold.",
    ),
    (
        "router",
        "Router",
        "Reflexes",
        "stage",
        "router.py",
        "Picks the first backend in the chain with a closed breaker and a free slot; busy is not broken.",
    ),
    (
        "hedger",
        "Hedger",
        "Reflexes",
        "stage",
        "router.py",
        "No first token by the backend's p95 TTFT? Fire the same request at the next backend; first token wins.",
    ),
    (
        "relay",
        "Stream relay",
        "Delivery",
        "stage",
        "pipeline.py",
        "Relays tokens to the client as Server-Sent Events from an async generator.",
    ),
    (
        "ledger_queue",
        "Ledger queue",
        "Delivery",
        "stage",
        "ledger.py",
        "Usage rows and cache writes go to a background queue, never the request path.",
    ),
    (
        "sqlite",
        "SQLite ledger",
        "Delivery",
        "store",
        "ledger.py",
        "One row per request, written in batches by a background task.",
    ),
]
SIDE = [
    (
        "redis",
        "Redis Stack",
        "store",
        "cache.py",
        "Exact cache, HNSW vector index, rate-limit buckets and budgets.",
    ),
    (
        "otel",
        "OpenTelemetry -> Jaeger",
        "store",
        "telemetry.py",
        "One trace per request, one span per stage, GenAI semantic conventions, OTLP export.",
    ),
    (
        "chaos",
        "Chaos controller",
        "side",
        "chaos.py",
        "Dev-only fault injection: latency, error rate, freeze a backend.",
    ),
]
GROUPS = ["Ingress", "Policy", "Memory", "Reflexes", "Backends", "Delivery"]


def _stage_status(state: AppState, node_id: str) -> str:
    status = "up"
    if node_id in ("exact_cache", "semantic_cache", "ratelimit", "budget") and not state.redis_ok:
        status = "degraded"
    if node_id == "semantic_cache" and not state.embedder.loaded:
        status = "degraded"
    if node_id == "sqlite":
        status = (state.health.get("sqlite") or {}).get("status", "unknown")
    return str(status)


def _pipeline_nodes(state: AppState) -> list[dict[str, Any]]:
    return [
        {
            "id": node_id,
            "label": label,
            "group": group,
            "kind": kind,
            "status": _stage_status(state, node_id),
            "about": about,
            "source": f"{SOURCE}/{source}",
            "stats": state.metrics.stage_stats(node_id),
        }
        for node_id, label, group, kind, source, about in PIPELINE
    ]


def _backend_node(state: AppState, cfg: GatewayConfig, name: str) -> dict[str, Any]:
    health = state.health
    b = cfg.backends[name]
    rt = state.router.runtimes.get(name)
    h = health.get(f"backend:{name}") or {}
    breaker = rt.breaker.current_state() if rt else "closed"
    return {
        "id": f"backend:{name}",
        "label": b.label,
        "group": "Backends",
        "kind": "backend",
        "status": h.get("status", "unknown"),
        "about": f"{b.provider} serving {b.model} at {b.base_url or b.api_base}",
        "source": f"{SOURCE}/backends/{'litellm_adapter' if b.kind == 'litellm' else 'openai_compat'}.py",
        "config": {
            "kind": b.kind,
            "base_url": b.base_url or b.api_base,
            "model": b.model,
            "slots": b.slots,
            "defaults": b.defaults,
            "chains": [mn for mn, mc in cfg.models.items() if name in mc.chain],
        },
        "breaker": {
            "state": breaker,
            "retry_after_s": round(rt.breaker.retry_after(), 1) if rt else 0,
            "open_seconds": cfg.breaker.open_seconds,
            "trips": rt.breaker.trips if rt else 0,
            # the share of failures in the breaker's rolling window (trips at >= 50%)
            "window_failure_rate": rt.breaker.window_failure_rate() if rt else None,
            "failure_rate_threshold": cfg.breaker.failure_rate,
        },
        "slots": {"capacity": rt.capacity, "in_flight": rt.in_flight} if rt else None,
        # what the serving engine reports about itself (vLLM), and whether routing avoids it
        "engine": rt.engine.as_dict() if rt and rt.engine else None,
        "saturated": rt.saturated() if rt else False,
        "stats": {
            "ttft_p50_ms": _r(rt.p(0.5)) if rt else None,
            "ttft_p95_ms": _r(rt.p(0.95)) if rt else None,
            "hedge_after_ms": _r(state.router.hedge_delay_ms(rt, cfg)) if rt else None,
            **(rt.counters if rt else {}),
        },
    }


def _side_nodes(state: AppState) -> list[dict[str, Any]]:
    def status_of(component: str) -> str:
        return str((state.health.get(component) or {}).get("status", "unknown"))

    return [
        {
            "id": node_id,
            "label": label,
            "group": "Side systems",
            "kind": kind,
            "status": {"redis": status_of("redis"), "otel": status_of("jaeger")}.get(
                node_id, "up" if state.chaos.enabled else "disabled"
            ),
            "about": about,
            "source": f"{SOURCE}/{source}",
            "stats": {},
        }
        for node_id, label, kind, source, about in SIDE
    ]


def build(state: AppState) -> dict[str, Any]:
    cfg = state.config.get()
    edges: list[dict[str, Any]] = []
    # every backend any model chains to, once, in chain order
    seen = list(dict.fromkeys(name for model in cfg.models.values() for name in model.chain))
    nodes = [
        *_pipeline_nodes(state),
        *(_backend_node(state, cfg, name) for name in seen),
        *_side_nodes(state),
    ]

    stage_configs = {
        # keys by their public id: the raw key never leaves aegis.security
        "ratelimit": {
            "keys": {v.id: {"tenant": v.tenant, "tpm": v.tpm} for v in cfg.keys.values()}
        },
        "budget": {"tenants": {v.tenant: v.budget.model_dump() for v in cfg.keys.values()}},
        "exact_cache": {"ttl_seconds": cfg.cache.ttl_seconds},
        "safety": cfg.safety.model_dump(),
        "semantic_cache": {
            "threshold": cfg.cache.semantic_threshold,
            "max_tokens": cfg.cache.semantic_max_tokens,
            "embed_budget_ms": cfg.cache.embed_budget_ms,
        },
        "router": cfg.router.model_dump() | {"chains": {k: v.chain for k, v in cfg.models.items()}},
        "hedger": {
            "hedge_min_ms": cfg.router.hedge_min_ms,
            "hedge_default_ms": cfg.router.hedge_default_ms,
        },
        "embedder": {"model": state.embedder.model_name, "dim": 384},
        "coalescer": {
            "in_flight": len(state.coalescer.flights),
            "coalesced_total": state.coalescer.coalesced_total,
        },
        "ledger_queue": {
            "pending": state.ledger.pending,
            "rows_written": state.ledger.rows_written,
            "batches": state.ledger.batches,
            "background_jobs_pending": state.recorder.pending,
        },
    }
    for node in nodes:
        if node["id"] in stage_configs:
            node["config"] = stage_configs[node["id"]]

    chain = [
        "client",
        "auth",
        "guardrails",
        "ratelimit",
        "budget",
        "exact_cache",
        "semantic_cache",
        "coalescer",
        "safety",
        "router",
        "hedger",
    ]
    for src, dst in itertools.pairwise(chain):
        edges.append({"id": f"{src}->{dst}", "source": src, "target": dst, "kind": "flow"})
    for name in seen:
        edges.append(
            {
                "id": f"router->backend:{name}",
                "source": "router",
                "target": f"backend:{name}",
                "kind": "flow",
            }
        )
        edges.append(
            {
                "id": f"hedger->backend:{name}",
                "source": "hedger",
                "target": f"backend:{name}",
                "kind": "hedge",
            }
        )
        edges.append(
            {
                "id": f"backend:{name}->relay",
                "source": f"backend:{name}",
                "target": "relay",
                "kind": "flow",
            }
        )
    edges += [
        {"id": "exact_cache->relay", "source": "exact_cache", "target": "relay", "kind": "cache"},
        {
            "id": "semantic_cache->relay",
            "source": "semantic_cache",
            "target": "relay",
            "kind": "cache",
        },
        {"id": "coalescer->relay", "source": "coalescer", "target": "relay", "kind": "coalesce"},
        {"id": "relay->client", "source": "relay", "target": "client", "kind": "response"},
        {"id": "relay->ledger_queue", "source": "relay", "target": "ledger_queue", "kind": "flow"},
        {
            "id": "ledger_queue->sqlite",
            "source": "ledger_queue",
            "target": "sqlite",
            "kind": "flow",
        },
        {
            "id": "semantic_cache->embedder",
            "source": "semantic_cache",
            "target": "embedder",
            "kind": "side",
        },
        {"id": "exact_cache->redis", "source": "exact_cache", "target": "redis", "kind": "side"},
        {"id": "ratelimit->redis", "source": "ratelimit", "target": "redis", "kind": "side"},
        {"id": "chaos->router", "source": "chaos", "target": "router", "kind": "side"},
        {"id": "relay->otel", "source": "relay", "target": "otel", "kind": "side"},
    ]
    for e in edges:
        e["status"] = "broken" if _edge_broken(e, nodes) else "ok"
    root = state.settings.project_dir
    for node in nodes:
        node["source_abs"] = str(root / node["source"])
    return {
        "nodes": nodes,
        "edges": edges,
        "groups": [*GROUPS, "Side systems"],
        "config_reloads": state.config.reloads,
        "config_error": state.config.last_error,
    }


def _edge_broken(edge: dict[str, Any], nodes: list[dict[str, Any]]) -> bool:
    by_id = {n["id"]: n for n in nodes}
    for end in (edge["source"], edge["target"]):
        node = by_id.get(end)
        if node and node.get("breaker", {}).get("state") == "open":
            return True
        if node and node.get("status") == "down":
            return True
    return False


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
