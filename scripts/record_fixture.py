"""Records real gateway traffic into the website's offline replay fixture.

Connects to /ws/events, drives a short real demo through the gateway (requests, a paraphrase, a
duplicate storm, a burst, a flood, a PII prompt, a hedge), captures every event with its real
timing, then snapshots /api/health, /api/architecture, /api/metrics, /api/requests and
/api/tenants. Nothing is synthesised: the fixture is a recording of this machine.

    make record-fixture    (with `make dev` running)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "replay" / "fixture.json"


async def chat(client: httpx.AsyncClient, prompt: str, key: str = "prahari-demo-key", no_cache: bool = False) -> None:
    headers = {"authorization": f"Bearer {key}"}
    if no_cache:
        headers["cache-control"] = "no-cache"
    await client.post("/v1/chat/completions", headers=headers, json={
        "model": "prahari-chat", "temperature": 0, "max_tokens": 60, "stream": False,
        "messages": [{"role": "user", "content": prompt}]})


async def drive(client: httpx.AsyncClient) -> None:
    stamp = int(time.time())
    await chat(client, f"In one sentence, what is an AI gateway? ({stamp})")
    await chat(client, f"In one sentence, what is an AI gateway? ({stamp})")
    await chat(client, f"in one sentence what is an ai gateway ({stamp})")
    await client.post("/api/playground/duplicate", json={"n": 8})
    await client.post("/api/playground/burst", json={"n": 6})
    await client.post("/api/playground/flood", json={"n": 6})
    await chat(client, "Email jane.doe@example.com about her refund, sign as Priya Sharma.", no_cache=True)
    r = await client.post("/api/scenarios/slow_primary")
    if r.status_code == 200:
        await asyncio.sleep(8)


async def main() -> None:
    events: list[dict] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as client:
        async with websockets.connect(BASE.replace("http", "ws") + "/ws/events", max_size=None) as ws:
            await ws.recv()  # hello

            async def collect() -> None:
                while True:
                    e = json.loads(await ws.recv())
                    if e.get("type") != "heartbeat":
                        events.append(e)

            task = asyncio.create_task(collect())
            await drive(client)
            await asyncio.sleep(2)
            task.cancel()
        snapshots = {
            "health": (await client.get("/api/health")).json(),
            "architecture": (await client.get("/api/architecture")).json(),
            "metrics": (await client.get("/api/metrics?window=300&step=5")).json(),
            "requests": (await client.get("/api/requests?limit=50")).json(),
            "tenants": (await client.get("/api/tenants")).json(),
        }
    events.sort(key=lambda e: e["ts"])
    fixture = {"recorded_at": time.time(), "duration_s": round(events[-1]["ts"] - events[0]["ts"], 1) if events else 0,
               "snapshots": snapshots, "events": events}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, separators=(",", ":")))
    print(f"recorded {len(events)} real events over {fixture['duration_s']} s -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    asyncio.run(main())
