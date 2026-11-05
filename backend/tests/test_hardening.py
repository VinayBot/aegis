"""Distributed tracing across services, and slowing down key guessing."""

from __future__ import annotations

from tests.conftest import Gateway

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


async def test_a_callers_traceparent_continues_into_the_gateway(gateway: Gateway) -> None:
    """NEXUS (or any app) sends its W3C trace context: the gateway's spans join that trace."""
    r = await gateway.chat("traced across services", headers={"traceparent": TRACEPARENT})
    assert r.status_code == 200 and r.headers["x-prahari-trace-id"] == TRACE_ID
    spans = [s for s in gateway.spans.get_finished_spans() if s.name == "POST /v1/chat/completions"]
    root = spans[-1]
    assert format(root.context.trace_id, "032x") == TRACE_ID
    assert root.parent is not None and format(root.parent.span_id, "016x") == "00f067aa0ba902b7"


async def test_without_a_traceparent_each_request_starts_its_own_trace(gateway: Gateway) -> None:
    a = (await gateway.chat("own trace a")).headers["x-prahari-trace-id"]
    b = (await gateway.chat("own trace b")).headers["x-prahari-trace-id"]
    assert a != b and TRACE_ID not in (a, b)


async def test_repeated_wrong_keys_are_slowed_down(gateway: Gateway) -> None:
    for _ in range(20):
        assert (await gateway.chat("guess", key="wrong-key")).status_code == 401
    blocked = await gateway.chat("guess", key="wrong-key")
    assert (
        blocked.status_code == 429 and blocked.json()["error"]["code"] == "too_many_auth_failures"
    )
    assert int(blocked.headers["retry-after"]) >= 1
    # the right key is refused too until the window passes: the address is cooling down
    assert (await gateway.chat("now honest", key="k-demo")).status_code == 429
    c = gateway.client
    assert (
        await c.post("/v1/auth/token", headers={"authorization": "Bearer k-demo"})
    ).status_code == 429
    gateway.state.auth_failures.failures.clear()  # the window passed
    assert (await gateway.chat("after the window", key="k-demo")).status_code == 200


async def test_missing_keys_do_not_count_as_guesses(gateway: Gateway) -> None:
    for _ in range(25):
        await gateway.client.post("/v1/chat/completions", json={"messages": []})
    assert gateway.state.auth_failures.retry_after("127.0.0.1") == 0.0
