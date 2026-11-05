"""Exact and semantic response caches in Redis Stack.

Exact: hash(tenant, model, messages, effective params) -> stored completion.

Semantic: partitioned by hash(tenant, model, params, every message except the last), so a
paraphrase only matches inside the same conversation and settings. The vector is the last user
message, and only when it is under `semantic_max_tokens`: long RAG-style prompts are dominated by
retrieved context, so they get exact matching only. HNSW index, cosine, 384-d. A hit needs
cosine similarity >= the threshold (0.95 to start).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from redis.asyncio import Redis

from prahari.embedder import DIM
from prahari.tokens import estimate_text, message_text


@dataclass
class CachedCompletion:
    content: str
    finish_reason: str
    usage: dict[str, int]
    backend: str
    created: float
    kind: str = "exact"  # "exact" | "semantic"
    similarity: float | None = None
    matched_prompt: str | None = None


@dataclass
class CacheKeys:
    exact: str
    partition: str
    last_user: str | None  # None: not eligible for the semantic cache
    meta: dict[str, Any] = field(default_factory=dict)


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _norm(message: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": str(message.get("role")), "content": message_text(message)}
    # tool turns: two conversations that differ only in a tool call or result are different
    for extra in ("tool_calls", "tool_call_id", "name"):
        if message.get(extra):
            out[extra] = message[extra]
    return out


def build_keys(
    tenant: str,
    model: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    semantic_max_tokens: int,
) -> CacheKeys:
    norm: list[dict[str, Any]] = [_norm(m) for m in messages]
    exact = _digest({"t": tenant, "m": model, "msgs": norm, "p": params})
    partition = _digest({"t": tenant, "m": model, "p": params, "ctx": norm[:-1]})[:24]
    last_user = None
    if norm and norm[-1]["role"] == "user":
        text = norm[-1]["content"].strip()
        if text and estimate_text(text) < semantic_max_tokens:
            last_user = text
    return CacheKeys(exact=exact, partition=partition, last_user=last_user)


class ResponseCache:
    def __init__(self, redis: Redis | None, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix
        self.index = f"{prefix}:semidx"
        self.index_ready = False

    def _ek(self, digest: str) -> str:
        return f"{self.prefix}:ec:{digest}"

    def _sk(self, digest: str) -> str:
        return f"{self.prefix}:sc:{digest}"

    async def ensure_index(self) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.execute_command("FT.INFO", self.index)
        except Exception:
            await self.redis.execute_command(
                "FT.CREATE",
                self.index,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                f"{self.prefix}:sc:",
                "SCHEMA",
                "partition",
                "TAG",
                "vec",
                "VECTOR",
                "HNSW",
                "6",
                "TYPE",
                "FLOAT32",
                "DIM",
                str(DIM),
                "DISTANCE_METRIC",
                "COSINE",
            )
        self.index_ready = True

    async def get_exact(self, digest: str) -> CachedCompletion | None:
        if self.redis is None:
            return None
        raw = await self.redis.get(self._ek(digest))
        if raw is None:
            return None
        data = json.loads(raw)
        return CachedCompletion(kind="exact", **data)

    async def get_semantic(
        self, partition: str, vector: np.ndarray, threshold: float
    ) -> tuple[CachedCompletion | None, float | None]:
        """Returns (hit or None, best similarity seen) so misses still report how close they were."""
        if self.redis is None or not self.index_ready:
            return None, None
        query = f"(@partition:{{{partition}}})=>[KNN 1 @vec $v AS dist]"
        res = await self.redis.execute_command(
            "FT.SEARCH",
            self.index,
            query,
            "PARAMS",
            "2",
            "v",
            vector.astype(np.float32).tobytes(),
            "SORTBY",
            "dist",
            "RETURN",
            "2",
            "dist",
            "payload",
            "DIALECT",
            "2",
        )
        values = _first_result(res)
        if values is None:
            return None, None
        similarity = 1.0 - float(values["dist"])
        if similarity < threshold:
            return None, similarity
        payload = values["payload"]
        data = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        return CachedCompletion(kind="semantic", similarity=similarity, **data), similarity

    async def put(
        self, keys: CacheKeys, completion: CachedCompletion, ttl: int, vector: np.ndarray | None
    ) -> None:
        if self.redis is None:
            return
        payload = {
            "content": completion.content,
            "finish_reason": completion.finish_reason,
            "usage": completion.usage,
            "backend": completion.backend,
            "created": time.time(),
        }
        await self.redis.set(self._ek(keys.exact), json.dumps(payload), ex=ttl)
        if vector is not None and keys.last_user is not None and self.index_ready:
            sem = dict(payload, matched_prompt=keys.last_user)
            skey = self._sk(keys.exact)
            await self.redis.hset(
                skey,
                mapping={
                    "partition": keys.partition,
                    "vec": vector.astype(np.float32).tobytes(),
                    "payload": json.dumps(sem),
                },
            )
            await self.redis.expire(skey, ttl)

    async def stats(self) -> dict[str, Any]:
        if self.redis is None:
            return {"exact_entries": 0, "semantic_entries": 0}
        exact = 0
        async for _ in self.redis.scan_iter(match=f"{self.prefix}:ec:*", count=500):
            exact += 1
        semantic = 0
        try:
            info = await self.redis.execute_command("FT.INFO", self.index)
            pairs = (
                info if isinstance(info, dict) else dict(zip(info[::2], info[1::2], strict=False))
            )
            semantic = int(pairs.get(b"num_docs") or pairs.get("num_docs") or 0)
        except Exception:
            pass
        return {"exact_entries": exact, "semantic_entries": semantic}

    async def flush(self) -> int:
        if self.redis is None:
            return 0
        removed = 0
        for pattern in (f"{self.prefix}:ec:*", f"{self.prefix}:sc:*"):
            async for key in self.redis.scan_iter(match=pattern, count=500):
                removed += await self.redis.delete(key)
        return removed


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _first_result(res: Any) -> dict[str, Any] | None:
    """The first FT.SEARCH hit's fields, from either reply shape: redis-py 8 parses it into a
    dict (`results` -> `extra_attributes`); older clients return the flat RESP2 list
    [total, id, [field, value, ...], ...]."""
    if isinstance(res, dict):
        results = res.get(b"results", res.get("results")) or []
        if not results:
            return None
        attrs = results[0].get(b"extra_attributes", results[0].get("extra_attributes")) or {}
        return {_text(k): v for k, v in attrs.items()}
    if not res or int(res[0]) == 0:
        return None
    fields = res[2]
    return {_text(fields[i]): fields[i + 1] for i in range(0, len(fields), 2)}
