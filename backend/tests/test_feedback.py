"""User feedback: stored per tenant, metered, and sent to Langfuse as a score on the trace."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.conftest import Gateway, free_port, serve, stop


async def answer(gateway: Gateway, prompt: str, key: str = "k-demo") -> str:
    r = await gateway.chat(prompt, key=key)
    return r.headers["x-aegis-request-id"]


def auth(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


async def test_a_tenant_rates_its_own_answer(gateway: Gateway) -> None:
    rid = await answer(gateway, "rate me")
    c = gateway.client
    r = await c.post(
        "/v1/feedback", json={"request_id": rid, "rating": "up"}, headers=auth("k-demo")
    )
    assert r.status_code == 200 and r.json()["rating"] == "up"
    # the vote is stored, counted per tenant and metered
    tenants = (await c.get("/api/tenants")).json()
    demo = next(t for t in tenants if t["tenant"] == "demo")
    assert demo["feedback_up"] >= 1
    await asyncio.sleep(0.1)
    assert (
        'aegis_feedback_total{backend="primary",model="aegis-chat",rating="up"}'
        in (await c.get("/metrics")).text
    )
    # changing your mind replaces the vote
    await c.post("/v1/feedback", json={"request_id": rid, "rating": "down"}, headers=auth("k-demo"))
    rows = await gateway.state.ledger.query(
        "SELECT value FROM feedback WHERE request_id = ?", (rid,)
    )
    assert rows == [{"value": 0}]


async def test_feedback_is_tenant_scoped_and_validated(gateway: Gateway) -> None:
    rid = await answer(gateway, "someone else's answer")
    c = gateway.client
    other = await c.post(
        "/v1/feedback", json={"request_id": rid, "rating": "up"}, headers=auth("k-other")
    )
    assert other.status_code == 404  # another tenant's answer does not exist for you
    assert (
        await c.post("/v1/feedback", json={"request_id": rid, "rating": "up"})
    ).status_code == 401
    bad = await c.post(
        "/v1/feedback", json={"request_id": rid, "rating": "meh"}, headers=auth("k-demo")
    )
    assert bad.status_code == 422
    admin = await c.post(
        "/v1/feedback", json={"request_id": rid, "rating": "up"}, headers=auth("k-admin")
    )
    assert admin.status_code == 200


async def test_feedback_becomes_a_langfuse_score(gateway: Gateway) -> None:
    received: list[dict[str, Any]] = []

    async def scores(request: Request) -> JSONResponse:
        received.append(
            {"auth": request.headers.get("authorization"), "body": await request.json()}
        )
        return JSONResponse({"id": "score_1"})

    port = free_port()
    server, task = await serve(
        Starlette(routes=[Route("/api/public/scores", scores, methods=["POST"])]), port
    )
    try:
        s = gateway.state.settings
        s.langfuse_host = f"http://127.0.0.1:{port}"
        s.langfuse_public_key, s.langfuse_secret_key = "pk-lf-t", SecretStr("sk-lf-t")
        rid = await answer(gateway, "score me")
        r = await gateway.client.post(
            "/v1/feedback",
            json={"request_id": rid, "rating": "down", "comment": "too vague"},
            headers=auth("k-demo"),
        )
        assert r.json()["sent_to_langfuse"] is True
        for _ in range(40):
            if received:
                break
            await asyncio.sleep(0.05)
        assert received, "no score reached Langfuse"
        body = received[0]["body"]
        assert body["traceId"] == r.json()["trace_id"] and body["name"] == "user-feedback"
        assert (
            body["value"] == 0 and body["dataType"] == "BOOLEAN" and body["comment"] == "too vague"
        )
        assert received[0]["auth"] == "Basic " + base64.b64encode(b"pk-lf-t:sk-lf-t").decode()
    finally:
        await stop(server, task)
