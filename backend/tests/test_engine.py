"""Engine-aware routing: the gateway reads vLLM's own metrics and routes around saturation."""

from __future__ import annotations

import time

import pytest

from prahari.engine import EngineStats, metrics_url, parse_vllm
from tests.conftest import Gateway
from tests.fake_backend import FakeControl

SAMPLE = """# HELP vllm:num_requests_running Number of requests in model execution batches.
vllm:num_requests_running{engine="0",model_name="qwen2.5-0.5b"} 3.0
vllm:num_requests_waiting{engine="0",model_name="qwen2.5-0.5b"} 5.0
vllm:kv_cache_usage_perc{engine="0",model_name="qwen2.5-0.5b"} 0.42
vllm:prefix_cache_queries_total{engine="0",model_name="qwen2.5-0.5b"} 2496.0
vllm:prefix_cache_hits_total{engine="0",model_name="qwen2.5-0.5b"} 1648.0
vllm:generation_tokens_total{engine="0",model_name="qwen2.5-0.5b"} 3157.0
"""


def test_vllm_metrics_are_parsed() -> None:
    e = parse_vllm(SAMPLE, at=100.0)
    assert e is not None and e.running == 3 and e.waiting == 5 and e.kv_cache_usage == 0.42
    assert e.prefix_cache_hit_rate == round(1648 / 2496, 4)
    assert parse_vllm("# not vllm\nfoo 1") is None
    assert metrics_url("http://h:8090/v1") == "http://h:8090/metrics"


def saturate(gateway: Gateway, name: str, waiting: float = 9, kv: float = 0.1) -> None:
    rt = gateway.state.router.runtimes[name]
    rt.cfg.engine_metrics = True
    rt.engine = EngineStats(
        running=8, waiting=waiting, kv_cache_usage=kv, prefix_cache_hit_rate=None, at=time.time()
    )


async def test_a_saturated_engine_is_routed_around(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    saturate(gateway, "primary")
    r = await gateway.chat("engine says it is queueing", headers={"cache-control": "no-cache"})
    assert r.headers["x-prahari-backend"] == "fallback" and fakes["primary"].calls == 0
    events = [e for e in gateway.state.bus.recent if e["type"] == "engine.saturated"]
    assert events and events[-1]["backend"] == "primary" and events[-1]["waiting"] == 9


@pytest.mark.parametrize("kv", [0.97])
async def test_a_full_kv_cache_also_counts(
    gateway: Gateway, fakes: dict[str, FakeControl], kv: float
) -> None:
    saturate(gateway, "primary", waiting=0, kv=kv)
    r = await gateway.chat("kv cache nearly full", headers={"cache-control": "no-cache"})
    assert r.headers["x-prahari-backend"] == "fallback"


async def test_a_saturated_engine_still_serves_when_it_is_the_only_one(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    saturate(gateway, "primary")
    gateway.state.router.runtimes["fallback"].breaker.force("open", "test")
    r = await gateway.chat("only the busy one is left", headers={"cache-control": "no-cache"})
    assert r.status_code == 200 and r.headers["x-prahari-backend"] == "primary"


async def test_stale_engine_stats_are_ignored(
    gateway: Gateway, fakes: dict[str, FakeControl]
) -> None:
    rt = gateway.state.router.runtimes["primary"]
    rt.cfg.engine_metrics = True
    rt.engine = EngineStats(
        running=8, waiting=99, kv_cache_usage=1.0, prefix_cache_hit_rate=None, at=time.time() - 60
    )
    r = await gateway.chat("old stats say nothing about now", headers={"cache-control": "no-cache"})
    assert r.headers["x-prahari-backend"] == "primary"
