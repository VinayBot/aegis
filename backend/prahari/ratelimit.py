"""Tokens-per-minute limits per API key: a token bucket in Redis, updated atomically in Lua.

A request reserves its estimated prompt tokens plus its completion budget up front. When it
finishes, the difference between reserved and actual tokens is settled (refund or charge), so
the bucket tracks real usage. If Redis is down, an in-process bucket with the same rules takes
over and health reports the gateway as degraded.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from redis.asyncio import Redis

# KEYS[1] bucket  ARGV: capacity, refill_per_ms, cost
# Returns {allowed, tokens_left, retry_after_ms}
_TAKE = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or capacity
local ts = tonumber(b[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = math.ceil((cost - tokens) / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], 120000)
return {allowed, tostring(tokens), retry}
"""

# Settle the difference between reserved and actual tokens. Can go negative: an overrun is
# paid back from the next minute's refill.
_SETTLE = """
local cur = tonumber(redis.call('HGET', KEYS[1], 'tokens'))
if cur == nil then return 0 end
redis.call('HSET', KEYS[1], 'tokens', cur - tonumber(ARGV[1]))
return 1
"""


@dataclass
class Decision:
    allowed: bool
    tokens_left: float
    retry_after_s: float
    backend: str  # "redis" | "memory"


class RateLimiter:
    def __init__(self, redis: Redis | None, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix
        self._memory: dict[str, tuple[float, float]] = {}
        self._take = redis.register_script(_TAKE) if redis is not None else None
        self._settle = redis.register_script(_SETTLE) if redis is not None else None

    def _key(self, api_key: str) -> str:
        return f"{self.prefix}:rl:{api_key}"

    async def take(self, api_key: str, tpm: int, cost: int, redis_ok: bool = True) -> Decision:
        rate = tpm / 60000.0
        if self._take is not None and redis_ok:
            try:
                allowed, left, retry_ms = await self._take(
                    keys=[self._key(api_key)], args=[tpm, rate, cost]
                )
                return Decision(bool(allowed), float(left), int(retry_ms) / 1000.0, "redis")
            except Exception:
                pass
        return self._take_memory(api_key, tpm, rate, cost)

    async def settle(self, api_key: str, delta: int, redis_ok: bool = True) -> None:
        if delta == 0:
            return
        if self._settle is not None and redis_ok:
            try:
                await self._settle(keys=[self._key(api_key)], args=[delta])
                return
            except Exception:
                pass
        if api_key in self._memory:
            tokens, ts = self._memory[api_key]
            self._memory[api_key] = (tokens - delta, ts)

    def _take_memory(self, api_key: str, tpm: int, rate: float, cost: int) -> Decision:
        now = time.monotonic() * 1000
        tokens, ts = self._memory.get(api_key, (float(tpm), now))
        tokens = min(float(tpm), tokens + max(0.0, now - ts) * rate)
        if tokens >= cost:
            self._memory[api_key] = (tokens - cost, now)
            return Decision(True, tokens - cost, 0.0, "memory")
        self._memory[api_key] = (tokens, now)
        return Decision(False, tokens, math.ceil((cost - tokens) / rate) / 1000.0, "memory")
