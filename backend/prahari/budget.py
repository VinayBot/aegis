"""Per-tenant monthly budgets in est. cloud-equivalent USD.

Local inference has no bill; spend here is the counterfactual cost of the same tokens on the
reference cloud model in the YAML. Soft limit: the response carries a warning header. Hard
limit: the request is refused before it reaches a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass
class BudgetStatus:
    spent: float
    soft: float
    hard: float

    @property
    def over_soft(self) -> bool:
        return self.spent >= self.soft

    @property
    def over_hard(self) -> bool:
        return self.spent >= self.hard


class Budgets:
    def __init__(self, redis: Redis | None, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix
        self._memory: dict[str, float] = {}

    def _key(self, tenant: str) -> str:
        return f"{self.prefix}:budget:{tenant}:{time.strftime('%Y%m')}"

    async def spent(self, tenant: str, redis_ok: bool = True) -> float:
        if self.redis is not None and redis_ok:
            try:
                value = await self.redis.get(self._key(tenant))
                return float(value) if value is not None else 0.0
            except Exception:
                pass
        return self._memory.get(tenant, 0.0)

    async def status(
        self, tenant: str, soft: float, hard: float, redis_ok: bool = True
    ) -> BudgetStatus:
        return BudgetStatus(await self.spent(tenant, redis_ok), soft, hard)

    async def charge(self, tenant: str, usd: float, redis_ok: bool = True) -> None:
        if usd <= 0:
            return
        if self.redis is not None and redis_ok:
            try:
                key = self._key(tenant)
                await self.redis.incrbyfloat(key, usd)
                await self.redis.expire(key, 40 * 86400)
                return
            except Exception:
                pass
        self._memory[tenant] = self._memory.get(tenant, 0.0) + usd

    async def reset(self, tenant: str) -> None:
        self._memory.pop(tenant, None)
        if self.redis is not None:
            await self.redis.delete(self._key(tenant))
