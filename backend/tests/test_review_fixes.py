"""Regression tests for the findings of the independent code review (docs/PEER_AUDIT.md)."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from prahari.config import SafetyConfig
from prahari.security import digest
from tests.conftest import Gateway, free_port, serve, stop
from tests.fake_backend import FakeControl, GuardControl, build_guard_app


@pytest.mark.parametrize(
    "gateway", [{"env": "production", "allow_plaintext_keys": False}], indirect=True
)
async def test_production_ignores_plaintext_keys(gateway: Gateway) -> None:
    """The repo's demo keys are public: in production only sha256: entries authenticate."""
    assert (await gateway.chat("plaintext key", key="k-demo")).status_code == 401
    cfg = gateway.state.config.get()
    raw = cfg.model_dump()
    raw["keys"][f"sha256:{digest('prod-secret')}"] = {
        "id": "prod",
        "tenant": "prod",
        "tpm": 10000,
        "budget": {"soft": 1, "hard": 2},
    }
    gateway.state.config.replace(type(cfg).model_validate(raw))
    assert (await gateway.chat("hashed key", key="prod-secret")).status_code == 200


async def test_a_non_string_role_is_a_400_not_a_500(gateway: Gateway) -> None:
    r = await gateway.client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": ["user"], "content": "hi"}]},
        headers={"authorization": "Bearer k-demo"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


async def test_an_unexpected_error_is_a_recorded_500(
    gateway: Gateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prahari import guardrails

    async def boom(_: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(guardrails, "check", boom)
    r = await gateway.chat("trigger a bug")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal"
    ends = [e for e in gateway.state.bus.recent if e["type"] == "request.end"]
    assert ends and ends[-1]["http_status"] == 500


async def test_websocket_cancel_before_the_first_token_settles_everything(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    fakes["primary"].first_token_delay = 1.0
    url = gateway.url.replace("http", "ws") + "/v1/ws/chat?key=k-demo"
    async with websockets.connect(url) as ws:
        body = {"model": "prahari-chat", "messages": [{"role": "user", "content": "slow start"}]}
        await ws.send(json.dumps({"type": "chat", "id": "c1", "body": body}))
        await asyncio.sleep(0.2)  # inside routing, waiting for the first token
        await ws.send(json.dumps({"type": "cancel", "id": "c1"}))
        while json.loads(await ws.recv())["type"] != "cancelled":
            pass
    await asyncio.sleep(0.5)
    ends = [e for e in gateway.state.bus.recent if e["type"] == "request.end"]
    assert ends[-1]["http_status"] == 499 and "client_closed" in (ends[-1]["error"] or "")
    assert gateway.state.coalescer.flights == {}  # no orphan flight for later requests to join
    assert all(rt.in_flight == 0 for rt in gateway.state.router.runtimes.values())
    again = await gateway.chat("slow start")  # an identical request is served, not stuck
    assert again.status_code == 200


async def test_dev_loopback_is_not_trusted_from_another_website(gateway: Gateway) -> None:
    c = gateway.client
    assert (
        await c.get("/api/config", headers={"origin": "https://evil.example"})
    ).status_code == 401
    assert (
        await c.get("/api/config", headers={"origin": "http://127.0.0.1:5173"})
    ).status_code == 200
    ws_url = gateway.url.replace("http", "ws") + "/ws/events"
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(ws_url, origin="https://evil.example"):  # type: ignore[arg-type]
            pass


async def test_llama_guard_sees_system_and_tool_messages(gateway: Gateway) -> None:
    guard = GuardControl(input_verdict="unsafe\nS9")
    port = free_port()
    server, task = await serve(build_guard_app(guard), port)
    try:
        cfg = gateway.state.config.get()
        safety = SafetyConfig(enabled=True, base_url=f"http://127.0.0.1:{port}/v1", timeout_ms=500)
        gateway.state.config.replace(cfg.model_copy(update={"safety": safety}))
        r = await gateway.client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "HAZARDOUS SYSTEM INSTRUCTION"},
                    {"role": "user", "content": "Do it."},
                ]
            },
            headers={"authorization": "Bearer k-demo"},
        )
        assert r.status_code == 400
        assert "HAZARDOUS SYSTEM INSTRUCTION" in guard.seen[0][0]["content"]
    finally:
        await stop(server, task)


async def test_max_completion_tokens_is_honoured_and_every_request_is_capped(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    await gateway.chat("newer parameter", max_completion_tokens=50)
    assert fakes["primary"].requests[-1]["max_tokens"] == 50
    cfg = gateway.state.config.get()
    raw = cfg.model_dump()
    raw["backends"]["primary"]["defaults"] = {"temperature": 0.0}  # a backend with no max_tokens
    gateway.state.config.replace(type(cfg).model_validate(raw))
    gateway.state.router.sync(gateway.state.config.get())
    await gateway.chat("no cap given", headers={"cache-control": "no-cache"})
    assert fakes["primary"].requests[-1]["max_tokens"] == cfg.limits.max_output_tokens


async def test_a_bad_hot_reload_does_not_fail_readiness(gateway: Gateway) -> None:
    gateway.state.config.last_error = "while scanning a simple key"
    r = await gateway.client.get("/readyz")
    assert r.status_code == 200 and r.json()["config_error"]
