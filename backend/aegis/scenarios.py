"""Guided Chaos Lab scenarios and Playground load helpers.

Every request here is a real HTTP call to the gateway's own /v1/chat/completions; every chaos
step is a real fault. Each step is narrated as a `scenario.step` event so the website can show
what is happening while the graph and charts show it happen.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from aegis.state import AppState

DEMO_KEY = "aegis-demo-key"
FLOOD_KEY = "aegis-flood-key"
_counter = itertools.count(1)

TOPICS = [
    "why a circuit breaker needs a half-open state",
    "what request hedging trades off",
    "how a semantic cache can return a wrong answer",
    "why busy is not the same as broken",
    "what a token bucket rate limiter allows",
    "why streaming improves perceived latency",
    "how request coalescing saves money",
    "what an OpenTelemetry span records",
    "why local models need a fallback",
    "what a p95 latency tells you",
    "how to pick a cache similarity threshold",
    "why retries can make an outage worse",
]


async def fire(
    state: AppState,
    prompt: str,
    *,
    key: str = DEMO_KEY,
    no_cache: bool = False,
    max_tokens: int = 60,
    model: str = "aegis-chat",
    client_name: str = "playground",
    temperature: float | None = 0.0,
) -> dict[str, Any]:
    headers = {
        "authorization": f"Bearer {key}",
        "x-aegis-summary": "1",
        "x-aegis-client": client_name,
    }
    if no_cache:
        headers["cache-control"] = "no-cache"
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        body["temperature"] = temperature
    start = time.perf_counter()
    result: dict[str, Any] = {"prompt": prompt}
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=2.0)) as client,
            client.stream(
                "POST", f"{state.settings.self_url}/v1/chat/completions", json=body, headers=headers
            ) as resp,
        ):
            result["status"] = resp.status_code
            result["x_cache"] = resp.headers.get("x-cache")
            result["backend"] = resp.headers.get("x-aegis-backend")
            result["request_id"] = resp.headers.get("x-aegis-request-id")
            result["retry_after"] = resp.headers.get("retry-after")
            if resp.status_code != 200:
                result["error"] = json.loads(await resp.aread() or b"{}").get("error")
            else:
                text, event = [], None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:") and line[5:].strip() != "[DONE]":
                        obj = json.loads(line[5:])
                        if event == "aegis":
                            result["summary"] = obj
                        elif event == "error":
                            result["error"] = obj.get("error")
                        else:
                            for c in obj.get("choices") or []:
                                text.append((c.get("delta") or {}).get("content") or "")
                        event = None
                result["text"] = "".join(text)
    except Exception as exc:
        result["status"] = 0
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["ms"] = round((time.perf_counter() - start) * 1000, 1)
    return result


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    s = r.get("summary") or {}
    return {
        "status": r.get("status"),
        "x_cache": r.get("x_cache"),
        "backend": r.get("backend"),
        "request_id": r.get("request_id"),
        "ms": r.get("ms"),
        "retry_after": r.get("retry_after"),
        "hedged": s.get("hedged"),
        "failovers": s.get("failovers"),
        "error": (r.get("error") or {}).get("code")
        if isinstance(r.get("error"), dict)
        else r.get("error"),
    }


async def burst(
    state: AppState, n: int, distinct: bool = True, prompt: str | None = None, no_cache: bool = True
) -> list[dict[str, Any]]:
    run = next(_counter)
    prompts = [
        prompt or f"In one sentence: {TOPICS[i % len(TOPICS)]}. (burst {run}.{i})"
        if distinct
        else prompt or f"In one sentence: {TOPICS[0]}."
        for i in range(n)
    ]
    results = await asyncio.gather(
        *(fire(state, p, no_cache=no_cache, max_tokens=50) for p in prompts)
    )
    return [_brief(r) for r in results]


async def duplicate(state: AppState, n: int, prompt: str | None = None) -> list[dict[str, Any]]:
    run = uuid.uuid4().hex[:6]
    p = prompt or f"In two sentences, explain request coalescing. (storm {run})"
    results = await asyncio.gather(*(fire(state, p, max_tokens=60) for _ in range(n)))
    return [_brief(r) for r in results]


async def flood(state: AppState, n: int) -> list[dict[str, Any]]:
    """n concurrent requests on the 1,500 tokens/min key, each reserving a 300-token completion
    budget: the first few fit in the bucket, the rest get 429 with Retry-After. (Sequential tiny
    requests would never trip it: unused reservations are refunded when a request finishes.)"""
    run = next(_counter)
    results = await asyncio.gather(
        *(
            fire(state, f"Say OK. (flood {run}.{i})", key=FLOOD_KEY, no_cache=True, max_tokens=300)
            for i in range(n)
        )
    )
    return [_brief(r) for r in results]


# ---------------------------------------------------------------------------- guided scenarios

Step = Callable[[], Awaitable[Any]]


class ScenarioRunner:
    def __init__(self, state: AppState) -> None:
        self.s = state
        self.running: dict[str, asyncio.Task[None]] = {}
        self.catalogue: dict[str, tuple[str, str, Callable[[str], Awaitable[None]]]] = {
            "primary_crash": (
                "Primary crashes",
                "Freeze the MLX primary mid-service; watch hedging,"
                " the breaker opening, traffic rerouting, and the heal.",
                self._primary_crash,
            ),
            "traffic_burst": (
                "Traffic burst",
                "Twelve requests at once: slots fill, overflow spills"
                " to the fallback after the 2 s slot wait.",
                self._traffic_burst,
            ),
            "slow_primary": (
                "Slow primary -> hedging",
                "Add 4 s of latency to the primary; the hedger"
                " races the fallback at the primary's p95 TTFT.",
                self._slow_primary,
            ),
            "duplicate_storm": (
                "Duplicate storm -> coalescing",
                "Ten identical requests at once share one upstream call.",
                self._duplicate_storm,
            ),
            "ratelimit_flood": (
                "Rate-limit flood",
                "A key with a 1,500 tokens/min budget sends eight requests: 429s with Retry-After.",
                self._ratelimit_flood,
            ),
            "pii_prompt": (
                "PII in prompt",
                "A prompt with an email, phone number and name: redacted"
                " before the cache, the embedder or any model sees it.",
                self._pii,
            ),
        }

    def list(self) -> list[dict[str, Any]]:
        return [
            {"id": k, "title": t, "about": a, "running": k in self.running}
            for k, (t, a, _) in self.catalogue.items()
        ]

    def start(self, name: str) -> str:
        if name not in self.catalogue:
            raise KeyError(name)
        if name in self.running:
            raise RuntimeError(f"{name} is already running")
        run_id = f"{name}-{uuid.uuid4().hex[:6]}"
        fn = self.catalogue[name][2]

        async def run() -> None:
            try:
                await fn(run_id)
                self._say(run_id, name, "done", "Scenario complete.", status="done")
            except Exception as exc:
                self._say(run_id, name, "failed", f"Scenario failed: {exc}", status="failed")
            finally:
                self.running.pop(name, None)

        self.running[name] = asyncio.get_running_loop().create_task(run())
        return run_id

    def _say(
        self, run_id: str, name: str, step: str, text: str, status: str = "running", **data: Any
    ) -> None:
        self.s.bus.publish(
            "scenario.step",
            None,
            run_id=run_id,
            scenario=name,
            step=step,
            text=text,
            status=status,
            **data,
        )

    def _backend_for(self, role: int) -> tuple[str, str | None]:
        chain = self.s.config.get().models["aegis-chat"].chain
        name = chain[min(role, len(chain) - 1)]
        return name, self.s.config.get().backends[name].process

    async def _primary_crash(self, run_id: str) -> None:
        name = "primary_crash"
        primary, process = self._backend_for(0)
        chaos = self.s.chaos
        self._say(run_id, name, "baseline", f"Baseline: one request, served by {primary}.")
        r = await fire(self.s, f"In one sentence, what is a gateway? ({run_id})", no_cache=True)
        self._say(
            run_id,
            name,
            "baseline",
            f"Served by {r.get('backend')} in {r['ms']:.0f} ms.",
            result=_brief(r),
        )
        entry = chaos.apply(primary, "kill", True, process=process, revert_after=90)
        mode = (
            "SIGSTOP (process frozen)" if entry.get("mode") == "sigstop" else "connections refused"
        )
        self._say(run_id, name, "kill", f"{primary} killed: {mode}.")
        results = await asyncio.gather(
            *(
                fire(self.s, f"In one sentence: {TOPICS[i]}. ({run_id})", no_cache=True)
                for i in range(3)
            )
        )
        ok = sum(1 for x in results if x.get("status") == 200)
        self._say(
            run_id,
            name,
            "reroute",
            f"{ok}/3 requests answered while the primary is down "
            f"(backends: {sorted({str(x.get('backend')) for x in results})}).",
            results=[_brief(x) for x in results],
        )
        rt = self.s.router.runtimes[primary]
        for _ in range(40):
            if rt.breaker.current_state() == "open":
                break
            await asyncio.sleep(0.25)
        self._say(
            run_id,
            name,
            "breaker",
            f"Breaker on {primary}: {rt.breaker.current_state()}. "
            f"Traffic skips it for {rt.breaker.retry_after():.0f} s.",
        )
        r = await fire(self.s, f"In one sentence: {TOPICS[4]}. ({run_id})", no_cache=True)
        self._say(
            run_id,
            name,
            "skip",
            f"Next request goes straight to {r.get('backend')} ({r['ms']:.0f} ms, no wait).",
            result=_brief(r),
        )
        chaos.apply(primary, "kill", None, process=process)
        wait = rt.breaker.retry_after()
        self._say(
            run_id,
            name,
            "revive",
            f"{primary} revived. Breaker half-opens in {wait:.0f} s; "
            "one live request will probe it.",
            countdown_s=round(wait, 1),
        )
        await asyncio.sleep(wait + 0.3)
        r = await fire(self.s, f"In one sentence: {TOPICS[5]}. ({run_id})", no_cache=True)
        self._say(
            run_id,
            name,
            "heal",
            f"Probe served by {r.get('backend')}; breaker is now {rt.breaker.current_state()}.",
            result=_brief(r),
        )

    async def _traffic_burst(self, run_id: str) -> None:
        name = "traffic_burst"
        self._say(run_id, name, "fire", "Firing 12 distinct requests at once.")
        results = await burst(self.s, 12)
        by_backend: dict[str, int] = {}
        for r in results:
            by_backend[str(r.get("backend"))] = by_backend.get(str(r.get("backend")), 0) + 1
        self._say(
            run_id,
            name,
            "result",
            f"Served: {by_backend}. Statuses: {sorted({r['status'] for r in results})}.",
            results=results,
        )

    async def _slow_primary(self, run_id: str) -> None:
        name = "slow_primary"
        primary, process = self._backend_for(0)
        rt = self.s.router.runtimes[primary]
        self._say(run_id, name, "warm", f"Warm-up so {primary} has a TTFT history.")
        for i in range(5):
            await fire(self.s, f"Say the word {i}. ({run_id})", no_cache=True, max_tokens=5)
        hedge_ms = self.s.router.hedge_delay_ms(rt, self.s.config.get())
        self._say(
            run_id,
            name,
            "inject",
            f"{primary} p95 TTFT is {rt.p(0.95) or 0:.0f} ms, so the "
            f"hedger fires after {hedge_ms:.0f} ms. Adding 4000 ms of latency.",
        )
        self.s.chaos.apply(primary, "latency", 4000, process=process, revert_after=60)
        r = await fire(self.s, f"In one sentence: {TOPICS[1]}. ({run_id})", no_cache=True)
        s = r.get("summary") or {}
        self._say(
            run_id,
            name,
            "race",
            f"Hedged: {s.get('hedged')}; first token from "
            f"{r.get('backend')} after {s.get('ttft_ms')} ms.",
            result=_brief(r),
        )
        self.s.chaos.apply(primary, "latency", None, process=process)
        self._say(run_id, name, "clear", "Latency removed.")

    async def _duplicate_storm(self, run_id: str) -> None:
        name = "duplicate_storm"
        self._say(run_id, name, "fire", "Ten identical requests, sent at the same moment.")
        results = await duplicate(self.s, 10)
        leaders = [r for r in results if r.get("x_cache") != "coalesced"]
        self._say(
            run_id,
            name,
            "result",
            f"{len(results)} answers, {len(leaders)} upstream call(s); "
            f"{len(results) - len(leaders)} coalesced.",
            results=results,
        )

    async def _ratelimit_flood(self, run_id: str) -> None:
        name = "ratelimit_flood"
        self._say(run_id, name, "fire", "Eight requests on a key limited to 1,500 tokens/minute.")
        results = await flood(self.s, 8)
        limited = [r for r in results if r["status"] == 429]
        retry = limited[0]["retry_after"] if limited else None
        self._say(
            run_id,
            name,
            "result",
            f"{len(results) - len(limited)} allowed, {len(limited)} "
            f"rejected with 429 (Retry-After {retry} s).",
            results=results,
        )

    async def _pii(self, run_id: str) -> None:
        name = "pii_prompt"
        prompt = (
            "Draft a two-line reply to Priya Sharma (priya.sharma@example.com, +91 98765 43210)"
            f" about her refund. ({run_id})"
        )
        self._say(run_id, name, "send", f"Sending: {prompt}")
        r = await fire(self.s, prompt, no_cache=True, max_tokens=80)
        s = r.get("summary") or {}
        self._say(
            run_id,
            name,
            "result",
            f"Redacted before any model saw it: {s.get('redactions')}. "
            "The trace records counts only, never the values.",
            result=_brief(r),
            redactions=s.get("redactions"),
        )
