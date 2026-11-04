"""The request path. Every stage is its own OTel span and publishes enter/exit events:

  auth -> guardrails -> ratelimit -> budget -> exact_cache -> semantic_cache (embedder)
       -> coalescer -> router (slots, breakers, hedger) -> backend -> relay -> ledger_queue

Errors before the first token become proper HTTP statuses (401, 400, 429, 402, 503). After the
first token the response is committed; a backend dying mid-stream ends it with an SSE error
event, a token estimate, and no cache write.

Cost is an *est. cloud equivalent*: local inference has no bill. Each request is priced at the
model's reference price in the YAML; cache hits and coalesced followers record that price as
saved, because no upstream call was made for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Span

from aegis import guardrails, logcontext, security, telemetry
from aegis.cache import CachedCompletion, CacheKeys, build_keys
from aegis.coalesce import Flight
from aegis.config import GatewayConfig, KeyConfig, Price
from aegis.guardrails.output import StreamRedactor
from aegis.retry import full_jitter
from aegis.router import MidStreamError, NoBackendAvailable, Routed, UpstreamClientError
from aegis.state import AppState
from aegis.tokens import (
    ESTIMATE_MARGIN,
    estimate_messages,
    estimate_text,
    estimate_tools,
    message_text,
)

request_log = logging.getLogger("aegis.request")
COST_LABEL = "est. cloud equivalent"
ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})


class GatewayError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        outcome: str = "error",
        retry_after: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.outcome = outcome  # rejected | blocked | error
        self.retry_after = retry_after
        self.extra = extra or {}


@dataclass
class RequestContext:
    id: str
    model: str
    stream: bool
    no_cache: bool
    client: str
    root: Span
    trace_id: str
    started: float
    key_id: str = ""
    key_cfg: KeyConfig | None = None
    tenant: str = "?"
    tpm: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens_est: int = 0
    reserved: int = 0
    redactions: dict[str, int] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    similarity: float | None = None


@dataclass
class Prepared:
    status: int
    headers: dict[str, str]
    events: AsyncIterator[tuple[str, dict[str, Any]]] | None = None
    body: dict[str, Any] | None = None


def price_of(price: Price, tokens_in: int, tokens_out: int) -> float:
    return tokens_in * price.input_per_1m / 1e6 + tokens_out * price.output_per_1m / 1e6


def _redact_chunk(chunk: dict[str, Any], redactor: StreamRedactor) -> dict[str, Any]:
    """The chunk with its text passed through the streaming output redactor."""
    choices = chunk.get("choices")
    if not choices:
        return chunk
    new = []
    for choice in choices:
        delta = dict(choice.get("delta") or {})
        text = redactor.feed(delta.get("content") or "")
        if choice.get("finish_reason"):
            text += redactor.flush()
        if "content" in delta or text:
            delta["content"] = text
        new.append({**choice, "delta": delta})
    return {**chunk, "choices": new}


def _has_tool_calls(chunk: dict[str, Any]) -> bool:
    return any((c.get("delta") or {}).get("tool_calls") for c in chunk.get("choices") or [])


def _delta_content(chunk: dict[str, Any]) -> tuple[str, str | None]:
    text, finish = "", None
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        text += delta.get("content") or ""
        finish = choice.get("finish_reason") or finish
    return text, finish


class Pipeline:
    def __init__(self, state: AppState) -> None:
        self.s = state

    # ------------------------------------------------------------------ helpers

    @contextlib.asynccontextmanager
    async def _stage(
        self, ctx: RequestContext, name: str, **attrs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        bus = self.s.bus
        bus.publish("stage.enter", ctx.id, stage=name)
        rec: dict[str, Any] = {"outcome": "ok", "data": {}}
        t0 = time.perf_counter()
        with telemetry.stage_span(ctx.root, name, **attrs) as span:
            try:
                yield rec
            except BaseException:
                if rec["outcome"] == "ok":
                    rec["outcome"] = "error"
                raise
            finally:
                ms = round((time.perf_counter() - t0) * 1000, 2)
                span.set_attribute("aegis.outcome", rec["outcome"])
                for k, v in rec["data"].items():
                    if isinstance(v, (str, int, float, bool)):
                        span.set_attribute(f"aegis.{k}", v)
                ctx.stages.append(
                    {"stage": name, "ms": ms, "outcome": rec["outcome"], **rec["data"]}
                )
                bus.publish(
                    "stage.exit", ctx.id, stage=name, ms=ms, outcome=rec["outcome"], **rec["data"]
                )

    def _chunk(
        self, ctx: RequestContext, delta: dict[str, Any], finish: str | None = None
    ) -> dict[str, Any]:
        return {
            "id": ctx.id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": ctx.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    def _base_headers(self, ctx: RequestContext) -> dict[str, str]:
        return {
            "x-aegis-request-id": ctx.id,
            "x-aegis-trace-id": ctx.trace_id,
            "x-aegis-cost-label": COST_LABEL,
            **ctx.headers,
        }

    # ------------------------------------------------------------------ entry point

    async def handle(
        self,
        body: dict[str, Any],
        api_key: str | None,
        *,
        no_cache: bool = False,
        client: str = "api",
    ) -> Prepared:
        cfg = self.s.config.get()
        model = str(body.get("model") or "aegis-chat")
        ctx = RequestContext(
            id=f"req_{uuid.uuid4().hex[:16]}",
            model=model,
            stream=bool(body.get("stream")),
            no_cache=no_cache,
            client=client,
            root=telemetry.start_root(model, {}),
            trace_id="",
            started=time.perf_counter(),
        )
        ctx.trace_id = telemetry.trace_id_of(ctx.root)
        logcontext.bind(request_id=ctx.id, trace_id=ctx.trace_id)
        ctx.root.set_attributes(
            {"aegis.request_id": ctx.id, "aegis.client": client, "aegis.no_cache": no_cache}
        )
        messages = body.get("messages")
        preview = ""
        if isinstance(messages, list) and messages and isinstance(messages[-1], dict):
            preview = message_text(messages[-1])[:120]
        self.s.bus.publish(
            "request.start",
            ctx.id,
            model=model,
            client=client,
            stream=ctx.stream,
            no_cache=no_cache,
            trace_id=ctx.trace_id,
        )
        try:
            return await self._run(ctx, cfg, body, api_key, preview)
        except GatewayError as err:
            return self._fail(ctx, cfg, err)

    def _fail(self, ctx: RequestContext, cfg: GatewayConfig, err: GatewayError) -> Prepared:
        headers = self._base_headers(ctx)
        if err.retry_after is not None:
            headers["retry-after"] = str(max(1, round(err.retry_after + 0.49)))
        self._finish(
            ctx,
            cfg,
            status=err.outcome,
            http_status=err.status,
            backend=None,
            cache="miss",
            content="",
            usage=None,
            error=f"{err.code}: {err.message}",
            extra=err.extra,
        )
        return Prepared(
            err.status,
            headers,
            body={
                "error": {
                    "message": err.message,
                    "type": err.outcome,
                    "code": err.code,
                    "request_id": ctx.id,
                    "trace_id": ctx.trace_id,
                    **err.extra,
                }
            },
        )

    async def _run(
        self,
        ctx: RequestContext,
        cfg: GatewayConfig,
        body: dict[str, Any],
        api_key: str | None,
        preview: str,
    ) -> Prepared:
        s = self.s
        # -- auth
        async with self._stage(ctx, "auth") as rec:
            key_cfg = security.resolve_key(cfg, api_key)
            if key_cfg is None:
                rec["outcome"] = "rejected"
                raise GatewayError(
                    401, "invalid_api_key", "Unknown or missing API key.", outcome="rejected"
                )
            ctx.key_id, ctx.key_cfg = key_cfg.id, key_cfg
            ctx.tenant, ctx.tpm = key_cfg.tenant, key_cfg.tpm
            rec["data"].update(tenant=ctx.tenant, key_id=ctx.key_id)
        logcontext.bind(tenant=ctx.tenant)
        ctx.root.set_attributes(
            {
                "aegis.tenant": ctx.tenant,
                "aegis.key_id": ctx.key_id,
                "langfuse.user.id": ctx.tenant,
                "langfuse.trace.tags": [ctx.model, ctx.client],
            }
        )
        s.bus.publish("request.meta", ctx.id, tenant=ctx.tenant, preview=preview)

        if ctx.model not in cfg.models:
            raise GatewayError(
                404,
                "model_not_found",
                f"Unknown model {ctx.model!r}. Try one of {sorted(cfg.models)}.",
            )
        messages = body.get("messages")
        if (
            not isinstance(messages, list)
            or not messages
            or not all(isinstance(m, dict) for m in messages)
        ):
            raise GatewayError(400, "invalid_request", "`messages` must be a non-empty list.")
        self._check_limits(cfg, body, messages)

        # -- guardrails: PII redaction + prompt-injection check
        async with self._stage(ctx, "guardrails") as rec:
            outcome = await guardrails.check(messages)
            if outcome.blocked:
                rec["outcome"] = "blocked"
                rec["data"]["matched"] = outcome.matched or ""
                s.bus.publish(
                    "guardrail.block", ctx.id, reason=outcome.reason, matched=outcome.matched
                )
                raise GatewayError(
                    400,
                    "prompt_injection",
                    "Blocked by the prompt-injection guardrail.",
                    outcome="blocked",
                    extra={"matched": outcome.matched},
                )
            ctx.messages = outcome.messages
            ctx.redactions = outcome.redactions
            if outcome.redactions:
                rec["outcome"] = "redacted"
                rec["data"]["redacted"] = sum(outcome.redactions.values())
                s.bus.publish(
                    "guardrail.redact",
                    ctx.id,
                    entities=outcome.redactions,
                    preview=message_text(outcome.messages[-1])[:160],
                )
        ctx.params = cfg.effective_params(ctx.model, body)
        ctx.prompt_tokens_est = estimate_messages(ctx.messages) + estimate_tools(
            ctx.params.get("tools")
        )

        # -- rate limit (tokens per minute, per key)
        reserve = ctx.prompt_tokens_est + int(ctx.params.get("max_tokens") or 512)
        async with self._stage(ctx, "ratelimit") as rec:
            decision = await s.limiter.take(ctx.key_id, ctx.tpm, reserve, s.redis_ok)
            rec["data"].update(
                reserved=reserve, tokens_left=int(decision.tokens_left), store=decision.backend
            )
            if not decision.allowed:
                rec["outcome"] = "rejected"
                s.bus.publish(
                    "ratelimit.reject",
                    ctx.id,
                    tenant=ctx.tenant,
                    tpm=ctx.tpm,
                    retry_after=decision.retry_after_s,
                    reserved=reserve,
                )
                raise GatewayError(
                    429,
                    "rate_limited",
                    f"Over {ctx.tpm} tokens/minute for this key. Retry later.",
                    outcome="rejected",
                    retry_after=decision.retry_after_s,
                    extra={"tpm": ctx.tpm, "retry_after_s": round(decision.retry_after_s, 2)},
                )
        ctx.reserved = reserve

        # -- budget (est. cloud-equivalent USD per tenant per month)
        key_cfg = ctx.key_cfg
        assert key_cfg is not None
        async with self._stage(ctx, "budget") as rec:
            status = await s.budgets.status(
                ctx.tenant, key_cfg.budget.soft, key_cfg.budget.hard, s.redis_ok
            )
            rec["data"].update(spent=round(status.spent, 6), hard=status.hard)
            if status.over_hard:
                rec["outcome"] = "blocked"
                s.bus.publish(
                    "budget.block", ctx.id, tenant=ctx.tenant, spent=status.spent, hard=status.hard
                )
                raise GatewayError(
                    402,
                    "budget_exceeded",
                    f"Tenant {ctx.tenant!r} reached its hard budget "
                    f"(${status.hard:.4f} {COST_LABEL}).",
                    outcome="blocked",
                )
            if status.over_soft:
                rec["outcome"] = "warned"
                ctx.headers["x-aegis-budget-warning"] = (
                    f"{ctx.tenant} has used ${status.spent:.4f} of ${status.hard:.4f} ({COST_LABEL})"
                )
                s.bus.publish(
                    "budget.warn",
                    ctx.id,
                    tenant=ctx.tenant,
                    spent=status.spent,
                    soft=status.soft,
                    hard=status.hard,
                )

        keys = build_keys(
            ctx.tenant, ctx.model, ctx.messages, ctx.params, cfg.cache.semantic_max_tokens
        )
        vector = None
        if ctx.no_cache:
            s.bus.publish("cache.bypass", ctx.id, reason="Cache-Control: no-cache")
        else:
            # -- exact cache
            async with self._stage(ctx, "exact_cache") as rec:
                hit = None
                if s.redis_ok:
                    try:
                        hit = await s.cache.get_exact(keys.exact)
                    except Exception as exc:
                        rec["outcome"] = "skipped"
                        rec["data"]["error"] = type(exc).__name__
                else:
                    rec["outcome"] = "skipped"
                if hit is not None:
                    rec["outcome"] = "hit"
            if hit is not None:
                return self._serve_cached(ctx, cfg, hit)

            # -- semantic cache
            if keys.last_user and s.redis_ok and s.cache.index_ready:
                async with self._stage(ctx, "semantic_cache") as rec:
                    async with self._stage(ctx, "embedder") as erec:
                        budget_s = cfg.cache.embed_budget_ms / 1000
                        try:
                            vector = await asyncio.wait_for(
                                s.embedder.embed_one(keys.last_user), budget_s
                            )
                        except TimeoutError:
                            erec["outcome"] = "skipped"
                            erec["data"]["reason"] = (
                                f"embedder over {cfg.cache.embed_budget_ms} ms budget"
                            )
                    hit = None
                    if vector is not None:
                        try:
                            hit, best = await s.cache.get_semantic(
                                keys.partition, vector, cfg.cache.semantic_threshold
                            )
                            if best is not None:
                                rec["data"]["best_similarity"] = round(best, 4)
                                ctx.similarity = round(best, 4)
                        except Exception as exc:
                            rec["outcome"] = "skipped"
                            rec["data"]["error"] = type(exc).__name__
                    else:
                        rec["outcome"] = "skipped"
                    rec["data"]["threshold"] = cfg.cache.semantic_threshold
                    if hit is not None:
                        rec["outcome"] = "hit"
                if hit is not None:
                    return self._serve_cached(ctx, cfg, hit)
            elif keys.last_user is None:
                s.bus.publish(
                    "cache.semantic_ineligible",
                    ctx.id,
                    reason="last message is long (RAG-style) or not from the user",
                )
            s.bus.publish("cache.miss", ctx.id, best_similarity=ctx.similarity)

        # -- coalescer
        async with self._stage(ctx, "coalescer") as rec:
            flight, leader = s.coalescer.join(keys.exact, ctx.id)
            rec["outcome"] = "leader" if leader else "follower"
            if not leader:
                rec["data"]["leader"] = flight.leader_id
                s.bus.publish(
                    "coalesce.join",
                    ctx.id,
                    leader=flight.leader_id,
                    followers=len(flight.followers),
                )
        if not leader:
            return await self._follow(ctx, cfg, flight)

        # -- content safety (Llama Guard 3): leader only, so followers share one verdict
        if cfg.safety.enabled:
            try:
                await self._safety_input(ctx, cfg)
            except BaseException as exc:
                await flight.finish(exc)
                s.coalescer.release(flight)
                raise

        # -- router (+ hedger) -> backend
        try:
            async with self._stage(ctx, "router") as rec:
                routed = await self._route_with_retry(ctx, cfg, rec)
                rec["data"].update(
                    backend=routed.attempt.rt.name,
                    ttft_ms=round(routed.ttft_ms, 1),
                    failovers=len(routed.failovers),
                    hedged=routed.hedged,
                    slot_wait_ms=round(routed.slot_wait_ms, 1),
                )
        except NoBackendAvailable as exc:
            err = GatewayError(
                503,
                "no_backend_available",
                str(exc),
                retry_after=exc.retry_after,
                extra={"failovers": exc.failovers},
            )
            await flight.finish(err)
            s.coalescer.release(flight)
            raise err from None
        except UpstreamClientError as exc:
            err = GatewayError(502, "upstream_rejected", str(exc.error))
            await flight.finish(err)
            s.coalescer.release(flight)
            raise err from None
        except BaseException as exc:
            await flight.finish(exc)
            s.coalescer.release(flight)
            raise

        backend = routed.attempt.rt.name
        flight.info = {"backend": backend, "ttft_ms": routed.ttft_ms}
        flight.started.set()
        headers = self._base_headers(ctx)
        headers.update(
            {
                "x-cache": "bypass" if ctx.no_cache else "miss",
                "x-aegis-backend": backend,
                "x-aegis-hedged": "true" if routed.hedged else "false",
                "x-aegis-failovers": str(len(routed.failovers)),
            }
        )
        return Prepared(200, headers, events=self._lead(ctx, cfg, routed, flight, keys, vector))

    def _check_limits(
        self, cfg: GatewayConfig, body: dict[str, Any], messages: list[dict[str, Any]]
    ) -> None:
        """Hard input bounds: cheap checks that run before any guardrail, cache or model."""
        lim = cfg.limits
        if len(messages) > lim.max_messages:
            raise GatewayError(
                400,
                "too_many_messages",
                f"{len(messages)} messages; the limit is {lim.max_messages}.",
                outcome="rejected",
            )
        bad = sorted({str(m.get("role")) for m in messages if m.get("role") not in ROLES})
        if bad:
            raise GatewayError(400, "invalid_request", f"Unknown message role(s): {bad}.")
        for field_name in ("max_tokens", "max_completion_tokens"):
            value = body.get(field_name)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise GatewayError(
                    400, "invalid_request", f"`{field_name}` must be a positive integer."
                )
            if value > lim.max_output_tokens:
                raise GatewayError(
                    400,
                    "max_tokens_too_large",
                    f"`{field_name}` {value} is over this gateway's limit of "
                    f"{lim.max_output_tokens}.",
                    outcome="rejected",
                )
        tokens = estimate_messages(messages) + estimate_tools(body.get("tools"))
        if tokens > lim.max_prompt_tokens:
            raise GatewayError(
                400,
                "context_length_exceeded",
                f"The prompt is about {tokens} tokens; the limit is {lim.max_prompt_tokens}.",
                outcome="rejected",
                extra={"prompt_tokens_est": tokens},
            )

    async def _safety_input(self, ctx: RequestContext, cfg: GatewayConfig) -> None:
        sc = cfg.safety
        async with self._stage(ctx, "safety") as rec:
            v = await self.s.safety.classify(
                sc.base_url, sc.model, ctx.messages, sc.timeout_ms / 1000, sc.unsafe_threshold
            )
            rec["data"].update(classifier_ms=round(v.ms, 1), classifier=sc.model)
            if v.p_unsafe is not None:
                rec["data"]["p_unsafe"] = round(v.p_unsafe, 3)
            if v.borderline:
                rec["outcome"] = "borderline"
                rec["data"]["categories"] = ",".join(v.categories)
                self.s.bus.publish(
                    "safety.borderline", ctx.id, categories=v.labels, p_unsafe=v.p_unsafe
                )
            if v.error:
                rec["outcome"] = "skipped"
                rec["data"]["error"] = v.error
                ctx.root.set_attribute("aegis.safety.input", "skipped")
                self.s.bus.publish("safety.skipped", ctx.id, error=v.error, policy=sc.on_error)
                if sc.on_error == "block":
                    raise GatewayError(
                        503,
                        "safety_unavailable",
                        "The content-safety classifier is unavailable and this gateway fails "
                        "closed.",
                        retry_after=5,
                    )
                return
            ctx.root.set_attribute("aegis.safety.input", "safe" if v.safe else "unsafe")
            if not v.safe:
                rec["outcome"] = "blocked"
                rec["data"]["categories"] = ",".join(v.categories)
                self.s.bus.publish("safety.block", ctx.id, categories=v.labels)
                raise GatewayError(
                    400,
                    "unsafe_content",
                    "Blocked by the content-safety guardrail (Llama Guard 3).",
                    outcome="blocked",
                    extra={"categories": v.labels},
                )

    async def _route_with_retry(
        self, ctx: RequestContext, cfg: GatewayConfig, rec: dict[str, Any]
    ) -> Routed:
        """Route; when every backend failed (not merely open breakers), back off and retry the
        chain, never past the queue deadline."""
        rc = cfg.router
        deadline = time.monotonic() + rc.queue_deadline_seconds
        attempt = 0
        while True:
            try:
                return await self.s.router.route(
                    config=cfg,
                    model=ctx.model,
                    payload={"messages": ctx.messages, **ctx.params},
                    params=ctx.params,
                    root=ctx.root,
                    prompt_tokens=ctx.prompt_tokens_est,
                    request_id=ctx.id,
                )
            except NoBackendAvailable as exc:
                if attempt >= rc.chain_retries or not exc.failovers:
                    raise
                delay = full_jitter(attempt, rc.retry_base_ms / 1000, rc.retry_cap_ms / 1000)
                if time.monotonic() + delay >= deadline:
                    raise
                attempt += 1
                rec["data"]["chain_retries"] = attempt
                self.s.bus.publish(
                    "router.retry",
                    ctx.id,
                    attempt=attempt,
                    delay_ms=round(delay * 1000, 1),
                    reason=str(exc),
                )
                await asyncio.sleep(delay)

    def _checked_cache_put(
        self,
        ctx: RequestContext,
        cfg: GatewayConfig,
        content: str,
        keys: CacheKeys,
        completion: CachedCompletion,
        ttl: int,
        vector: Any,
    ) -> Any:
        """Output safety, off the request path: an answer enters the cache only once Llama Guard
        has classified the (prompt, answer) pair as safe, so an unsafe answer is never replayed."""
        s, sc = self.s, cfg.safety
        convo = [*ctx.messages, {"role": "assistant", "content": content}]

        async def put() -> None:
            v = await s.safety.classify(
                sc.base_url, sc.model, convo, 2 * sc.timeout_ms / 1000, sc.unsafe_threshold
            )
            if not v.safe:
                s.bus.publish("safety.output_flag", ctx.id, categories=v.labels)
                return
            if v.error:
                s.bus.publish("safety.output_unchecked", ctx.id, error=v.error, policy=sc.on_error)
                if sc.on_error == "block":
                    return
            await s.cache.put(keys, completion, ttl, vector)

        return put

    # ------------------------------------------------------------------ streaming paths

    async def _lead(
        self,
        ctx: RequestContext,
        cfg: GatewayConfig,
        routed: Routed,
        flight: Flight,
        keys: CacheKeys,
        vector: Any,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        s = self.s
        backend = routed.attempt.rt.name
        parts: list[str] = []
        usage: dict[str, Any] | None = None
        finish: str | None = None
        tool_calls = False
        redactor = StreamRedactor() if cfg.output_guard.redact else None
        status, error = "ok", None
        s.bus.publish("stage.enter", ctx.id, stage="relay")
        relay_span = telemetry.tracer().start_span(
            "aegis.relay",
            context=telemetry.trace.set_span_in_context(ctx.root),
            attributes={"aegis.stage": "relay", "aegis.backend": backend},
        )
        t0 = time.perf_counter()
        try:
            async for raw in s.router.relay(routed):
                if raw.get("usage"):
                    usage = raw["usage"]
                chunk = _redact_chunk(raw, redactor) if redactor is not None else raw
                text, fin = _delta_content(chunk)
                parts.append(text)
                finish = fin or finish
                tool_calls = tool_calls or _has_tool_calls(chunk)
                out = {**chunk, "id": ctx.id, "model": ctx.model}
                await flight.push(out)
                if chunk.get("choices"):
                    yield "chunk", out
            if redactor is not None and (tail := redactor.flush()):
                # the stream ended without a finish chunk: release the held-back word
                out = self._chunk(ctx, {"content": tail})
                parts.append(tail)
                await flight.push(out)
                yield "chunk", out
            await flight.finish()
        except MidStreamError as exc:
            status, error = "midstream_error", str(exc)
            await flight.finish(exc)
            s.bus.publish("stream.error", ctx.id, backend=backend, detail=str(exc.error)[:160])
            yield (
                "error",
                {
                    "error": {
                        "message": f"The backend failed mid-stream ({exc.backend}). "
                        "Tokens already sent cannot be replaced; partial answer.",
                        "type": "midstream_error",
                        "request_id": ctx.id,
                    }
                },
            )
        except (asyncio.CancelledError, GeneratorExit):
            status, error = "client_closed", "client disconnected"
            await flight.finish(ConnectionAbortedError("leader client disconnected"))
            raise
        finally:
            s.coalescer.release(flight)
            ms = round((time.perf_counter() - t0) * 1000, 2)
            ctx.stages.append({"stage": "relay", "ms": ms, "outcome": status})
            s.bus.publish("stage.exit", ctx.id, stage="relay", ms=ms, outcome=status)
            if status == "ok":
                telemetry.end_ok(relay_span)
            else:
                telemetry.end_error(relay_span, error or status)
            content = "".join(parts)
            summary = self._finish(
                ctx,
                cfg,
                status=status if status != "client_closed" else "error",
                http_status=200,
                backend=backend,
                cache="bypass" if ctx.no_cache else "miss",
                content=content,
                usage=usage,
                error=error,
                routed=routed,
                # a tool call is an instruction to act on this conversation, not a reusable answer
                cache_write=(keys, vector)
                if status == "ok" and content and not tool_calls
                else None,
                finish_reason=finish,
                extra={"output_redactions": redactor.counts}
                if redactor is not None and redactor.counts
                else None,
            )
        yield "summary", summary

    async def _follow(self, ctx: RequestContext, cfg: GatewayConfig, flight: Flight) -> Prepared:
        s = self.s
        await flight.started.wait()
        if flight.error is not None and not flight.chunks:
            err = flight.error
            if isinstance(err, GatewayError):
                raise GatewayError(
                    err.status,
                    err.code,
                    f"{err.message} (coalesced with {flight.leader_id})",
                    outcome=err.outcome,
                    retry_after=err.retry_after,
                )
            raise GatewayError(502, "coalesced_leader_failed", f"leader failed: {err}")
        backend = flight.info.get("backend", "?")
        headers = self._base_headers(ctx)
        headers.update(
            {
                "x-cache": "coalesced",
                "x-aegis-backend": backend,
                "x-aegis-coalesced-with": flight.leader_id,
            }
        )

        async def events() -> AsyncIterator[tuple[str, dict[str, Any]]]:
            parts: list[str] = []
            usage = None
            status, error = "ok", None
            s.bus.publish("stage.enter", ctx.id, stage="relay")
            t0 = time.perf_counter()
            try:
                async for chunk in flight.follow():
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    parts.append(_delta_content(chunk)[0])
                    if chunk.get("choices"):
                        yield "chunk", {**chunk, "id": ctx.id}
            except Exception as exc:
                status, error = "midstream_error", str(exc)
                yield (
                    "error",
                    {
                        "error": {
                            "message": f"Coalesced stream failed: {exc}",
                            "type": "midstream_error",
                            "request_id": ctx.id,
                        }
                    },
                )
            finally:
                ms = round((time.perf_counter() - t0) * 1000, 2)
                ctx.stages.append({"stage": "relay", "ms": ms, "outcome": status})
                s.bus.publish("stage.exit", ctx.id, stage="relay", ms=ms, outcome=status)
                summary = self._finish(
                    ctx,
                    cfg,
                    status=status,
                    http_status=200,
                    backend=backend,
                    cache="coalesced",
                    content="".join(parts),
                    usage=usage,
                    error=error,
                    coalesced_with=flight.leader_id,
                )
            yield "summary", summary

        return Prepared(200, headers, events=events())

    def _serve_cached(
        self, ctx: RequestContext, cfg: GatewayConfig, hit: CachedCompletion
    ) -> Prepared:
        s = self.s
        s.bus.publish(
            "cache.hit",
            ctx.id,
            kind=hit.kind,
            similarity=hit.similarity,
            matched_prompt=(hit.matched_prompt or "")[:120],
            original_backend=hit.backend,
        )
        headers = self._base_headers(ctx)
        headers.update(
            {
                "x-cache": "hit" if hit.kind == "exact" else "semantic",
                "x-aegis-backend": f"cache ({hit.backend})",
            }
        )
        if hit.similarity is not None:
            headers["x-aegis-similarity"] = f"{hit.similarity:.4f}"
        ctx.similarity = hit.similarity

        async def events() -> AsyncIterator[tuple[str, dict[str, Any]]]:
            s.bus.publish("stage.enter", ctx.id, stage="relay")
            t0 = time.perf_counter()
            yield "chunk", self._chunk(ctx, {"role": "assistant", "content": ""})
            words = hit.content.split(" ")
            for i in range(0, len(words), 3):
                piece = " ".join(words[i : i + 3]) + (" " if i + 3 < len(words) else "")
                yield "chunk", self._chunk(ctx, {"content": piece})
            yield "chunk", self._chunk(ctx, {}, hit.finish_reason or "stop")
            ms = round((time.perf_counter() - t0) * 1000, 2)
            ctx.stages.append({"stage": "relay", "ms": ms, "outcome": "ok"})
            s.bus.publish("stage.exit", ctx.id, stage="relay", ms=ms, outcome="ok")
            summary = self._finish(
                ctx,
                cfg,
                status="ok",
                http_status=200,
                backend=hit.backend,
                cache=hit.kind,
                content=hit.content,
                usage=hit.usage,
                error=None,
            )
            yield "summary", summary

        return Prepared(200, headers, events=events())

    # ------------------------------------------------------------------ bookkeeping

    def _finish(
        self,
        ctx: RequestContext,
        cfg: GatewayConfig,
        *,
        status: str,
        http_status: int,
        backend: str | None,
        cache: str,
        content: str,
        usage: dict[str, Any] | None,
        error: str | None,
        routed: Routed | None = None,
        cache_write: tuple[CacheKeys, Any] | None = None,
        coalesced_with: str | None = None,
        finish_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        s = self.s
        latency = round((time.perf_counter() - ctx.started) * 1000, 1)
        estimated = usage is None or not usage.get("completion_tokens")
        if (
            usage
            and usage.get("prompt_tokens") is not None
            and usage.get("completion_tokens") is not None
        ):
            tokens_in, tokens_out = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
            estimated = False
        else:
            tokens_in = ctx.prompt_tokens_est if ctx.messages else 0
            tokens_out = estimate_text(content)
        price = cfg.models[ctx.model].price if ctx.model in cfg.models else Price()
        reached_model = status in ("ok", "midstream_error") and cache in ("miss", "bypass")
        served_without_call = status == "ok" and cache in ("exact", "semantic", "coalesced")
        value = (
            price_of(price, tokens_in, tokens_out)
            if (reached_model or served_without_call)
            else 0.0
        )
        cost = value if reached_model else 0.0
        saved = value if served_without_call else 0.0
        upstream_tokens = tokens_in + tokens_out if reached_model else 0

        ttft = round(routed.ttft_ms, 1) if routed else None
        record = {
            "id": ctx.id,
            "ts": time.time(),
            "tenant": ctx.tenant,
            "key_id": ctx.key_id,
            "model": ctx.model,
            "backend": backend,
            "status": status,
            "http_status": http_status,
            "cache": cache,
            "coalesced": int(cache == "coalesced"),
            "hedged": int(bool(routed and routed.hedged)),
            "failovers": len(routed.failovers) if routed else 0,
            "tokens_in": tokens_in if status != "rejected" and status != "blocked" else 0,
            "tokens_out": tokens_out,
            "usage_estimated": int(estimated and bool(tokens_out)),
            "latency_ms": latency,
            "ttft_ms": ttft,
            "cost_est_usd": round(cost, 10),
            "saved_est_usd": round(saved, 10),
            "trace_id": ctx.trace_id,
            "error": error,
            "redactions": ctx.redactions or None,
        }
        if ctx.key_id and http_status not in (401,):
            s.ledger.record_request(record)

        # Settle the rate limit to what the backends actually processed; charge the budget.
        if ctx.reserved:
            delta = upstream_tokens - ctx.reserved
            key, redis_ok = ctx.key_id, s.redis_ok
            s.recorder.submit(lambda: s.limiter.settle(key, delta, redis_ok))
        if cost > 0:
            tenant, redis_ok = ctx.tenant, s.redis_ok
            s.recorder.submit(lambda: s.budgets.charge(tenant, cost, redis_ok))
        if cache_write is not None and s.redis_ok:
            keys, vector = cache_write
            ttl = cfg.cache.ttl_seconds
            completion = CachedCompletion(
                content=content,
                finish_reason=finish_reason or "stop",
                usage={
                    "prompt_tokens": tokens_in,
                    "completion_tokens": tokens_out,
                    "total_tokens": tokens_in + tokens_out,
                },
                backend=backend or "?",
                created=time.time(),
            )
            if cfg.safety.enabled and cfg.safety.check_output:
                s.recorder.submit(
                    self._checked_cache_put(ctx, cfg, content, keys, completion, ttl, vector)
                )
            else:
                s.recorder.submit(lambda: s.cache.put(keys, completion, ttl, vector))
            s.bus.publish("cache.write.queued", ctx.id, semantic=vector is not None)
        s.bus.publish("stage.enter", ctx.id, stage="ledger_queue")
        s.bus.publish("stage.exit", ctx.id, stage="ledger_queue", ms=0.0, outcome="queued")

        hedge_won = bool(routed and routed.hedged and routed.attempt.hedge)
        summary = {
            "request_id": ctx.id,
            "trace_id": ctx.trace_id,
            "status": status,
            "http_status": http_status,
            "tenant": ctx.tenant,
            "key_id": ctx.key_id,
            "model": ctx.model,
            "backend": backend,
            "cache": cache,
            "similarity": ctx.similarity,
            "coalesced": cache == "coalesced",
            "coalesced_with": coalesced_with,
            "hedged": bool(routed and routed.hedged),
            "hedge_won": hedge_won,
            "hedge_winner": routed.hedge_winner if routed else None,
            "failovers": len(routed.failovers) if routed else 0,
            "failover_detail": routed.failovers if routed else [],
            "tokens_in": record["tokens_in"],
            "tokens_out": tokens_out,
            "usage_estimated": bool(record["usage_estimated"]),
            "estimate_margin": ESTIMATE_MARGIN if record["usage_estimated"] else None,
            "latency_ms": latency,
            "ttft_ms": ttft,
            "cost_est_usd": record["cost_est_usd"],
            "saved_est_usd": record["saved_est_usd"],
            "cost_label": COST_LABEL,
            "price_reference": price.reference,
            "error": error,
            "redactions": ctx.redactions or None,
            "stages": ctx.stages,
            "client": ctx.client,
            **(extra or {}),
        }
        s.bus.publish(
            "request.end",
            ctx.id,
            **{k: v for k, v in summary.items() if k not in ("request_id", "stages")},
        )
        request_log.log(
            logging.WARNING if status not in ("ok", "rejected", "blocked") else logging.INFO,
            "request",
            extra={
                "status": status,
                "http_status": http_status,
                "model": ctx.model,
                "key_id": ctx.key_id or None,
                "backend": backend,
                "cache": cache,
                "latency_ms": latency,
                "ttft_ms": ttft,
                "tokens_in": record["tokens_in"],
                "tokens_out": tokens_out,
                "cost_est_usd": record["cost_est_usd"],
                "failovers": record["failovers"],
                "error": error,
            },
        )
        root = ctx.root
        root.set_attributes(
            telemetry._clean(
                {
                    "http.response.status_code": http_status,
                    "aegis.status": status,
                    "aegis.cache": cache,
                    "aegis.backend": backend,
                    "gen_ai.usage.input_tokens": tokens_in,
                    "gen_ai.usage.output_tokens": tokens_out,
                    "aegis.usage_estimated": bool(record["usage_estimated"]),
                    "aegis.cost_est_usd": record["cost_est_usd"],
                    "aegis.saved_est_usd": record["saved_est_usd"],
                    "aegis.hedged": summary["hedged"],
                    "aegis.failovers": summary["failovers"],
                }
            )
        )
        if routed is not None:
            telemetry.end_ok(
                routed.attempt.span,
                **{
                    "gen_ai.response.model": routed.attempt.rt.backend.upstream_model,
                    "gen_ai.usage.input_tokens": tokens_in,
                    "gen_ai.usage.output_tokens": tokens_out,
                    "gen_ai.usage.cost": record["cost_est_usd"],
                    "langfuse.observation.metadata.cost_label": COST_LABEL,
                    "langfuse.observation.metadata.usage_estimated": bool(
                        record["usage_estimated"]
                    ),
                    "gen_ai.response.finish_reasons": [
                        finish_reason or ("error" if error else "stop")
                    ],
                    "aegis.ttft_ms": ttft,
                },
            ) if status == "ok" else telemetry.end_error(routed.attempt.span, error or status)
        if status in ("ok",):
            telemetry.end_ok(root)
        else:
            telemetry.end_error(root, error or status)
        return summary
