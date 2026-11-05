"""The control plane end to end: every guided scenario, the playground load generators, cache and
budget administration, self-check, docs, traces and the PRAHARI assistant."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tests.conftest import Gateway
from tests.fake_backend import FakeControl


async def run_scenario(gateway: Gateway, name: str, timeout: float = 30) -> list[dict[str, Any]]:
    r = await gateway.client.post(f"/api/scenarios/{name}")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(int(timeout / 0.1)):
        steps = [
            e
            for e in gateway.state.bus.recent
            if e["type"] == "scenario.step" and e.get("run_id") == run_id
        ]
        if any(s.get("status") in ("done", "error") for s in steps):
            return steps
        await asyncio.sleep(0.1)
    raise AssertionError(f"{name} did not finish")


def texts(steps: list[dict[str, Any]]) -> str:
    return " | ".join(str(s.get("text")) for s in steps)


@pytest.mark.parametrize("name", ["traffic_burst", "duplicate_storm", "pii_prompt"])
async def test_guided_scenarios_complete(gateway: Gateway, name: str) -> None:
    steps = await run_scenario(gateway, name)
    assert steps[-1]["status"] == "done", texts(steps)


async def test_ratelimit_flood_scenario_reports_429s(gateway: Gateway) -> None:
    steps = await run_scenario(gateway, "ratelimit_flood")
    assert steps[-1]["status"] == "done"
    assert "rejected with 429" in texts(steps)


@pytest.mark.parametrize("gateway", [{"breaker__open_seconds": 1}], indirect=True)
async def test_primary_crash_scenario_fails_over_and_heals(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    steps = await run_scenario(gateway, "primary_crash", timeout=45)
    story = texts(steps)
    assert steps[-1]["status"] == "done", story
    assert "requests answered while the primary is down" in story
    assert "breaker is closed again" in story or "breaker is now closed" in story


async def test_slow_primary_scenario_hedges(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    steps = await run_scenario(gateway, "slow_primary", timeout=45)
    assert steps[-1]["status"] == "done", texts(steps)
    assert any(e["type"] == "hedge.fire" for e in gateway.state.bus.recent)


async def test_a_scenario_cannot_run_twice_at_once(gateway: Gateway) -> None:
    first = await gateway.client.post("/api/scenarios/traffic_burst")
    second = await gateway.client.post("/api/scenarios/traffic_burst")
    assert first.status_code == 200 and second.status_code == 409
    assert (await gateway.client.post("/api/scenarios/no_such_thing")).status_code == 404


async def test_playground_generators(gateway: Gateway, fakes: dict[str, FakeControl]) -> None:
    c = gateway.client
    burst = (await c.post("/api/playground/burst", json={"n": 4})).json()["results"]
    assert len(burst) == 4 and all(r["status"] == 200 for r in burst)
    dup = (await c.post("/api/playground/duplicate", json={"n": 5, "no_cache": True})).json()
    assert sorted(r["x_cache"] for r in dup["results"]).count("coalesced") >= 3
    flood = (await c.post("/api/playground/flood", json={"n": 6})).json()["results"]
    assert any(r["status"] == 429 for r in flood)


async def test_cache_and_budget_administration(gateway: Gateway) -> None:
    c = gateway.client
    await gateway.chat("cache admin probe")
    await asyncio.sleep(0.3)
    stats = (await c.get("/api/cache/stats")).json()
    assert stats["exact_entries"] >= 1
    tuned = (await c.post("/api/cache/tune", json={"semantic_threshold": 0.97})).json()
    assert tuned["cache"]["semantic_threshold"] == 0.97
    assert (await c.post("/api/cache/tune", json={"semantic_threshold": 0.2})).status_code == 422
    assert (await c.post("/api/cache/flush")).status_code == 200
    assert (await c.get("/api/cache/stats")).json()["exact_entries"] == 0
    assert (await c.post("/api/budgets/demo/reset")).status_code == 200
    cfg = (await c.get("/api/config")).json()
    assert cfg["config"]["cache"]["semantic_threshold"] == 0.97


async def test_selfcheck_runs_every_stage(gateway: Gateway) -> None:
    out = (await gateway.client.post("/api/selfcheck")).json()
    assert out["ok"] is True and out["generation"]["status"] == 200
    assert {s["stage"] for s in out["stages"]} >= {"embedder", "exact_cache", "auth", "router"}


async def test_request_detail_and_recent_events(gateway: Gateway) -> None:
    r = await gateway.chat("detail probe")
    request_id = r.headers["x-prahari-request-id"]
    await asyncio.sleep(0.6)
    detail = (await gateway.client.get(f"/api/requests/{request_id}")).json()
    assert detail["request"]["id"] == request_id and detail["events"]
    assert (await gateway.client.get("/api/requests/req_missing")).status_code == 404
    recent = (await gateway.client.get("/api/events/recent?limit=50")).json()
    assert any(e.get("request_id") == request_id for e in recent)
    assert (await gateway.client.get("/api/chaos")).json()["enabled"] is True


async def test_traces_fail_cleanly_without_jaeger(gateway: Gateway) -> None:
    gateway.state.settings.jaeger_query_url = "http://127.0.0.1:9"  # nothing listens there
    r = await gateway.client.get("/api/traces")
    assert r.status_code == 503 and "unreachable" in r.json()["detail"]


async def test_docs_are_served_and_unknown_slugs_404(gateway: Gateway) -> None:
    docs = (await gateway.client.get("/api/docs")).json()
    assert isinstance(docs, list)
    assert (await gateway.client.get("/api/docs/NOPE.md")).status_code == 404


async def sse(gateway: Gateway, question: str) -> list[dict[str, Any]]:
    r = await gateway.client.post(
        "/api/assistant/chat", json={"question": question, "history": []}, timeout=30
    )
    assert r.status_code == 200
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


async def test_assistant_answers_through_the_gateway(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    items = await sse(gateway, "What just happened to the primary?")  # a live question
    kinds = [i["type"] for i in items]
    assert kinds[0] == "sources" and "meta" in kinds and kinds[-1] == "done"
    sources = items[0]["sources"]
    assert any(s["n"] == "live" for s in sources)  # live state was part of the context
    meta = next(i for i in items if i["type"] == "meta")
    assert meta["status"] == 200 and meta["headers"]["x-prahari-request-id"]
    sent = fakes["primary"].requests[-1]["messages"]
    assert "What happened in the last" in sent[-1]["content"]  # the timeline came first


async def test_assistant_refuses_politely_without_grounding(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    items = await sse(gateway, "Who painted the Mona Lisa?")
    assert items[0]["grounded"] is False
    sent = fakes["primary"].requests[-1]["messages"]
    assert sent[-1]["content"].startswith("The project docs don't cover that")
