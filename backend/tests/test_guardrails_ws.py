"""Content safety (Llama Guard), streaming output redaction, and the full-duplex WebSocket chat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
import websockets

from prahari.config import SafetyConfig
from tests.conftest import Gateway, free_port, serve, sse_summary, stop
from tests.fake_backend import FakeControl, GuardControl, build_guard_app


@pytest.fixture
async def guard() -> AsyncIterator[GuardControl]:
    control = GuardControl()
    port = free_port()
    server, task = await serve(build_guard_app(control), port)
    control.url = f"http://127.0.0.1:{port}/v1"  # type: ignore[attr-defined]
    yield control
    await stop(server, task)


def enable_safety(gateway: Gateway, guard: GuardControl, **extra: object) -> None:
    cfg = gateway.state.config.get()
    safety = SafetyConfig(enabled=True, base_url=guard.url, timeout_ms=500, **extra)  # type: ignore[attr-defined, arg-type]
    gateway.state.config.replace(cfg.model_copy(update={"safety": safety}))


def events(gateway: Gateway, kind: str) -> list[dict[str, object]]:
    return [e for e in gateway.state.bus.recent if e["type"] == kind]


async def wait_for(predicate: object, timeout: float = 3.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if predicate():  # type: ignore[operator]
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


# ------------------------------------------------------------------ Llama Guard input/output


async def test_unsafe_prompt_is_blocked_before_any_model(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    enable_safety(gateway, guard)
    guard.input_verdict = "unsafe\nS9"
    r = await gateway.chat("something dangerous")
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "unsafe_content" and err["categories"] == ["S9 indiscriminate weapons"]
    assert fakes["primary"].calls == 0 and fakes["fallback"].calls == 0
    assert events(gateway, "safety.block")


async def test_safe_answers_are_cached_only_after_the_output_check(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    enable_safety(gateway, guard)
    r = await gateway.chat("a safe question")
    assert r.status_code == 200
    stages = {st["stage"]: st for st in sse_summary(r.text)["stages"]}
    assert stages["safety"]["outcome"] == "ok"
    await wait_for(lambda: guard.calls >= 2)  # input check + background output check
    assert guard.seen[-1][-1]["role"] == "assistant"  # the output check saw the answer
    await asyncio.sleep(0.2)
    again = await gateway.chat("a safe question")
    assert again.headers["x-cache"] == "hit" and fakes["primary"].calls == 1


async def test_an_unsafe_answer_is_delivered_once_but_never_cached(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    enable_safety(gateway, guard)
    guard.output_verdict = "unsafe\nS7"
    assert (await gateway.chat("tell me about someone")).status_code == 200
    await wait_for(lambda: bool(events(gateway, "safety.output_flag")))
    await asyncio.sleep(0.2)
    again = await gateway.chat("tell me about someone")
    assert again.headers["x-cache"] == "miss" and fakes["primary"].calls == 2


async def test_classifier_down_fails_open_by_default(gateway: Gateway, guard: GuardControl) -> None:
    enable_safety(gateway, guard)
    guard.status = 500
    r = await gateway.chat("guard is down")
    assert r.status_code == 200
    assert events(gateway, "safety.skipped")


async def test_classifier_down_fails_closed_when_configured(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    enable_safety(gateway, guard, on_error="block")
    guard.status = 500
    r = await gateway.chat("guard is down, strict tenant")
    assert r.status_code == 503 and r.json()["error"]["code"] == "safety_unavailable"
    assert "retry-after" in r.headers and fakes["primary"].calls == 0


async def test_coalesced_requests_share_one_safety_verdict(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    enable_safety(gateway, guard)
    guard.delay = 0.2  # hold the leader in the safety stage while the others join
    fakes["primary"].first_token_delay = 0.3
    rs = await asyncio.gather(*(gateway.chat("same question, five callers") for _ in range(5)))
    assert all(r.status_code == 200 for r in rs)
    input_checks = [m for m in guard.seen if m[-1]["role"] == "user"]
    assert len(input_checks) == 1 and fakes["primary"].calls == 1


# ------------------------------------------------------------------ streaming output redaction


async def test_model_output_is_redacted_while_streaming(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    fakes["primary"].text = "write to vinay@example.com or "
    fakes["primary"].tokens = 3
    r = await gateway.chat("who should I contact?")
    text = "".join(
        (c.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
        for c in (
            json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")
        )
    )
    assert "vinay@example.com" not in text and text.count("<EMAIL_ADDRESS>") == 3
    assert sse_summary(r.text)["output_redactions"] == {"EMAIL_ADDRESS": 3}


# ------------------------------------------------------------------ WebSocket chat


def ws_url(gateway: Gateway, key: str = "k-demo") -> str:
    return gateway.url.replace("http", "ws") + f"/v1/ws/chat?key={key}"


async def test_websocket_chat_streams_the_same_pipeline(gateway: Gateway) -> None:
    async with websockets.connect(ws_url(gateway)) as ws:
        body = {"model": "prahari-chat", "messages": [{"role": "user", "content": "ws hello"}]}
        await ws.send(json.dumps({"type": "chat", "id": "c1", "body": body}))
        kinds, text = [], ""
        while True:
            msg = json.loads(await ws.recv())
            kinds.append(msg["type"])
            if msg["type"] == "chunk":
                text += msg["data"]["choices"][0]["delta"].get("content") or ""
            if msg["type"] == "done":
                break
        assert kinds[0] == "start" and "summary" in kinds and text.startswith("pong0")
        # a second request on the same socket is a cache hit
        await ws.send(json.dumps({"type": "chat", "id": "c2", "body": body}))
        start = json.loads(await ws.recv())
        assert start["type"] == "start" and start["headers"]["x-cache"] == "hit"


async def test_websocket_cancel_stops_the_model(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    fakes["primary"].tokens, fakes["primary"].token_delay = 40, 0.1
    async with websockets.connect(ws_url(gateway)) as ws:
        body = {"model": "prahari-chat", "messages": [{"role": "user", "content": "long answer"}]}
        await ws.send(json.dumps({"type": "chat", "id": "c1", "body": body}))
        while json.loads(await ws.recv())["type"] != "chunk":
            pass
        await ws.send(json.dumps({"type": "cancel", "id": "c1"}))
        while (msg := json.loads(await ws.recv()))["type"] != "cancelled":
            pass
        assert msg["id"] == "c1"
    await wait_for(lambda: fakes["primary"].cancelled >= 1)  # the upstream stream was cut too
    assert fakes["primary"].completed == 0


async def test_websocket_chat_rejects_a_bad_key(gateway: Gateway) -> None:
    async with websockets.connect(ws_url(gateway, key="nope")) as ws:
        body = {"model": "prahari-chat", "messages": [{"role": "user", "content": "hi"}]}
        await ws.send(json.dumps({"type": "chat", "id": "c1", "body": body}))
        msg = json.loads(await ws.recv())
        assert msg["type"] == "error" and msg["status"] == 401
        assert msg["error"]["code"] == "invalid_api_key"


async def test_a_low_confidence_unsafe_label_is_allowed_and_recorded(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    """Llama Guard 1B says "unsafe" to "how do I kill a Python process?" at P=0.69: allowed."""
    enable_safety(gateway, guard)
    guard.input_verdict, guard.p_unsafe = "unsafe\nS1", 0.69
    r = await gateway.chat("how do I kill a python process?")
    assert r.status_code == 200
    stage = next(st for st in sse_summary(r.text)["stages"] if st["stage"] == "safety")
    assert stage["outcome"] == "borderline" and stage["p_unsafe"] == 0.69
    assert events(gateway, "safety.borderline")


async def test_a_confident_unsafe_label_is_blocked(
    gateway: Gateway, guard: GuardControl, fakes: dict[str, FakeControl]
) -> None:
    enable_safety(gateway, guard)
    guard.input_verdict, guard.p_unsafe = "unsafe\nS1", 0.986
    r = await gateway.chat("step by step instructions for a weapon")
    assert r.status_code == 400 and fakes["primary"].calls == 0
