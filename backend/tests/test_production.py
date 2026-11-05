"""Production hardening: secure defaults, access control, tenant isolation, input bounds, probes,
Prometheus, retries with backoff, native tool calling, and clean shutdown."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx
import pytest
import websockets

from aegis.security import digest
from tests.conftest import Gateway, sse_summary, stop
from tests.fake_backend import FakeControl

PROD = {"env": "production"}


def auth(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


# ------------------------------------------------------------------ access control


@pytest.mark.parametrize("gateway", [PROD], indirect=True)
async def test_production_control_plane_needs_an_admin_key(gateway: Gateway) -> None:
    """Secure by default: in production, loopback is not trusted and tenants are not admins."""
    c = gateway.client
    assert (await c.get("/api/architecture")).status_code == 401
    assert (await c.get("/api/architecture", headers=auth("k-demo"))).status_code == 403
    assert (await c.get("/api/architecture", headers=auth("k-admin"))).status_code == 200
    assert (await c.post("/api/cache/flush")).status_code == 401
    assert (await c.post("/api/cache/flush", headers=auth("k-demo"))).status_code == 403
    # public endpoints stay public
    for path in ("/healthz", "/api/health", "/api/scenarios"):
        assert (await c.get(path)).status_code == 200, path


@pytest.mark.parametrize("gateway", [{**PROD, "admin_key": "env-admin-secret"}], indirect=True)
async def test_admin_key_from_the_environment(gateway: Gateway) -> None:
    c = gateway.client
    assert (await c.get("/api/config", headers=auth("env-admin-secret"))).status_code == 200
    assert (await c.get("/api/config", headers=auth("env-admin-wrong"))).status_code == 401


@pytest.mark.parametrize("gateway", [PROD], indirect=True)
async def test_production_ignores_a_chaos_switch(gateway: Gateway) -> None:
    """The fixture asks for chaos; production refuses it even for an admin."""
    assert gateway.state.chaos.enabled is False
    r = await gateway.client.post(
        "/api/chaos",
        json={"backend": "primary", "action": "kill", "value": True},
        headers=auth("k-admin"),
    )
    assert r.status_code == 403


async def test_dev_loopback_is_the_local_operator(gateway: Gateway) -> None:
    """The demo site needs no key in dev: a caller on this machine acts as admin."""
    assert (await gateway.client.get("/api/architecture")).status_code == 200


@pytest.mark.parametrize("gateway", [PROD], indirect=True)
async def test_event_websocket_needs_an_admin(gateway: Gateway) -> None:
    ws_url = gateway.url.replace("http", "ws") + "/ws/events"
    # refused during the handshake (closed before accept), so the client sees HTTP 403
    with pytest.raises(websockets.exceptions.InvalidStatus) as info:
        async with websockets.connect(ws_url):
            pass
    assert info.value.response.status_code == 403
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(f"{ws_url}?key=k-demo"):  # a tenant key is not enough
            pass
    async with websockets.connect(f"{ws_url}?key=k-admin") as ws:
        hello = json.loads(await ws.recv())
    assert hello["type"] == "hello"


# ------------------------------------------------------------------ tenant isolation


async def test_a_tenant_never_sees_another_tenants_requests(gateway: Gateway) -> None:
    """No IDOR: a request id from another tenant is simply not found."""
    r = await gateway.chat("tenant isolation probe", key="k-demo")
    request_id = sse_summary(r.text)["request_id"]
    await asyncio.sleep(0.6)  # the ledger writes in the background
    c = gateway.client
    assert (await c.get(f"/api/requests/{request_id}", headers=auth("k-demo"))).status_code == 200
    assert (await c.get(f"/api/requests/{request_id}", headers=auth("k-other"))).status_code == 404
    rows = (await c.get("/api/requests", headers=auth("k-other"))).json()
    assert all(row["tenant"] == "other" for row in rows)
    tenants = (await c.get("/api/tenants", headers=auth("k-other"))).json()
    assert [t["tenant"] for t in tenants] == ["other"]


async def test_raw_api_keys_never_leave_the_gateway(gateway: Gateway) -> None:
    await gateway.chat("key hygiene probe", key="k-demo")
    await asyncio.sleep(0.6)
    c = gateway.client
    bodies = [
        (await c.get(path)).text
        for path in (
            "/api/config",
            "/api/tenants",
            "/api/requests",
            "/api/metrics",
            "/api/architecture",
            "/api/events/recent",
        )
    ]
    for body in bodies:
        assert "k-demo" not in body and "k-admin" not in body and "aegis-demo-key" not in body
    rows = await gateway.state.ledger.query("SELECT DISTINCT key_id FROM requests")
    assert rows and all(not r["key_id"].startswith("k-") for r in rows)
    redis_keys = [k async for k in gateway.state.redis.scan_iter(match="*:rl:*")]
    assert redis_keys and all(b"k-demo" not in k for k in redis_keys)


@pytest.mark.parametrize("gateway", [{}], indirect=True)
async def test_hashed_keys_authenticate(gateway: Gateway) -> None:
    """A `sha256:` entry holds no usable secret, yet the raw key still authenticates."""
    cfg = gateway.state.config.get()
    entry = f"sha256:{digest('hashed-secret')}"
    raw = cfg.model_dump()
    raw["keys"][entry] = {"tenant": "hashed", "tpm": 1000, "budget": {"soft": 1, "hard": 2}}
    gateway.state.config.replace(type(cfg).model_validate(raw))
    assert (await gateway.chat("hashed key", key="hashed-secret")).status_code == 200
    assert (await gateway.chat("hashed key", key="hashed-wrong")).status_code == 401
    assert hashlib.sha256(b"hashed-secret").hexdigest() in entry


# ------------------------------------------------------------------ input bounds


@pytest.mark.parametrize("gateway", [{"limits__max_body_bytes": 2000}], indirect=True)
async def test_oversized_body_is_refused_before_it_is_read_whole(gateway: Gateway) -> None:
    r = await gateway.chat("x" * 5000)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "request_too_large"


@pytest.mark.parametrize(
    "gateway",
    [
        {
            "limits__max_messages": 3,
            "limits__max_prompt_tokens": 50,
            "limits__max_output_tokens": 100,
        }
    ],
    indirect=True,
)
async def test_input_bounds(gateway: Gateway, fakes: dict[str, FakeControl]) -> None:
    c = gateway.client
    h = {**auth("k-demo")}

    async def post(body: dict[str, object]) -> httpx.Response:
        return await c.post("/v1/chat/completions", json={"model": "aegis-chat", **body}, headers=h)

    many = [{"role": "user", "content": "hi"}] * 4
    assert (await post({"messages": many})).json()["error"]["code"] == "too_many_messages"
    long = [{"role": "user", "content": "word " * 400}]
    r = await post({"messages": long})
    assert r.status_code == 400 and r.json()["error"]["code"] == "context_length_exceeded"
    r = await post({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5000})
    assert r.json()["error"]["code"] == "max_tokens_too_large"
    r = await post({"messages": [{"role": "wizard", "content": "hi"}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert fakes["primary"].calls == 0  # nothing reached a model


# ------------------------------------------------------------------ probes and metrics


async def test_liveness_and_readiness(gateway: Gateway) -> None:
    c = gateway.client
    assert (await c.get("/healthz")).json()["status"] == "alive"
    assert (await c.get("/readyz")).status_code == 200
    for rt in gateway.state.router.runtimes.values():
        rt.breaker.force("open", "test")
    r = await c.get("/readyz")
    assert r.status_code == 503 and r.json()["usable_backends"] == []


async def test_prometheus_metrics(gateway: Gateway) -> None:
    await gateway.chat("prometheus probe")
    await gateway.chat("prometheus probe")  # exact cache hit
    await asyncio.sleep(0.2)
    r = await gateway.client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert 'aegis_requests_total{backend="primary",cache="miss"' in text
    assert 'cache="exact"' in text
    assert "aegis_time_to_first_token_seconds_bucket" in text
    assert 'aegis_breaker_state{backend="primary"} 0.0' in text


@pytest.mark.parametrize("gateway", [PROD], indirect=True)
async def test_prometheus_needs_a_key_in_production(gateway: Gateway) -> None:
    assert (await gateway.client.get("/metrics")).status_code == 401
    assert (await gateway.client.get("/metrics", headers=auth("k-admin"))).status_code == 200


# ------------------------------------------------------------------ resilience


@pytest.mark.parametrize("gateway", [{"router__chain_retries": 1}], indirect=True)
async def test_whole_chain_failure_is_retried_after_jittered_backoff(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    """Both backends blip once: the chain is retried after a full-jitter backoff and succeeds."""
    fakes["primary"].fail_times = 1
    fakes["fallback"].fail_times = 1
    r = await gateway.chat("transient blip")
    assert r.status_code == 200
    assert fakes["primary"].calls == 2  # first pass failed, the retry succeeded
    retries = [e for e in gateway.state.bus.recent if e["type"] == "router.retry"]
    assert retries and 0 <= retries[-1]["delay_ms"] <= 50


async def test_no_retry_when_every_breaker_is_open(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    """Retrying cannot help an open breaker; the client gets 503 + Retry-After at once."""
    for rt in gateway.state.router.runtimes.values():
        rt.breaker.force("open", "test")
    t = time.perf_counter()
    r = await gateway.chat("all open")
    assert r.status_code == 503 and "retry-after" in r.headers
    assert time.perf_counter() - t < 1.0
    assert fakes["primary"].calls == 0


# ------------------------------------------------------------------ native tool calling


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }
]


async def test_tool_calls_pass_through_streaming_and_are_not_cached(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    fakes["primary"].tool_call = {"name": "get_weather", "arguments": {"city": "Pune"}}
    r = await gateway.chat("weather in Pune?", tools=TOOLS, tool_choice="auto")
    assert r.status_code == 200
    upstream = fakes["primary"].requests[-1]
    assert upstream["tools"] == TOOLS and upstream["tool_choice"] == "auto"
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    deltas = [c["choices"][0]["delta"] for c in frames if c.get("choices")]
    assert any(d.get("tool_calls") for d in deltas)
    args = "".join(
        tc["function"].get("arguments", "") for d in deltas for tc in d.get("tool_calls") or []
    )
    assert json.loads(args) == {"city": "Pune"}
    # a tool call is not a reusable answer: the same request goes upstream again
    r2 = await gateway.chat("weather in Pune?", tools=TOOLS, tool_choice="auto")
    assert r2.headers["x-cache"] in ("miss", "coalesced") and fakes["primary"].calls == 2


async def test_tool_calls_are_assembled_for_non_streaming_clients(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    fakes["primary"].tool_call = {"name": "get_weather", "arguments": {"city": "Delhi"}}
    r = await gateway.chat("weather in Delhi?", stream=False, tools=TOOLS)
    message = r.json()["choices"][0]["message"]
    assert r.json()["choices"][0]["finish_reason"] == "tool_calls"
    call = message["tool_calls"][0]
    assert call["id"] == "call_1" and call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Delhi"}


async def test_tools_are_part_of_the_cache_key(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    """Same question, different tools: a different request, never a cache hit."""
    await gateway.chat("plain question")
    r = await gateway.chat("plain question", tools=TOOLS)
    assert r.headers["x-cache"] == "miss"


# ------------------------------------------------------------------ shutdown


async def test_shutdown_is_not_held_open_by_an_idle_dashboard(gateway: Gateway) -> None:
    """Regression: an idle /ws/events client used to keep the server from ever exiting."""
    ws_url = gateway.url.replace("http", "ws") + "/ws/events"
    ws = await websockets.connect(ws_url)
    await ws.recv()  # hello
    started = time.perf_counter()
    await stop(gateway.server, gateway.task)  # type: ignore[attr-defined]
    server = gateway.task  # type: ignore[attr-defined]
    assert server.done(), "the server did not shut down"
    assert time.perf_counter() - started < 4.5
    await ws.close()
