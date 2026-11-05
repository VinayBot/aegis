"""OpenTelemetry: every request is one trace, every pipeline stage one span, exported over OTLP
HTTP to Jaeger and, when keys are configured, to Langfuse at the same time.

Langfuse (cloud, or self-hosted v3+) ingests OTLP at `{host}/api/public/otel` with HTTP Basic auth
(public key : secret key). The spans carry Langfuse's own attributes as well: the root span names
the trace and sets the user (the tenant) and tags; the upstream model call is marked as a
`generation` with its model, token usage and estimated cost, so Langfuse's cost and latency views
work without an SDK. Prompt and completion text is never exported: traces carry counts, never
content (the gateway's privacy rule; PII is redacted before any span exists anyway).

The upstream model call follows the GenAI semantic conventions: span name `chat {model}`,
`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.*`, `gen_ai.response.*` and
`gen_ai.usage.*`.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from prahari import __version__

if TYPE_CHECKING:
    from prahari.settings import Settings

_tracer: trace.Tracer | None = None
_provider: TracerProvider | None = None
exporters: list[str] = []  # where spans go, for /api/health and the docs


@dataclass(frozen=True)
class OtlpTarget:
    name: str
    endpoint: str  # full .../v1/traces URL
    headers: dict[str, str]


def langfuse_target(settings: Settings) -> OtlpTarget | None:
    """Langfuse's OTLP endpoint and Basic auth, or None when its keys are not configured."""
    if not settings.langfuse_public_key or settings.langfuse_secret_key is None:
        return None
    secret = settings.langfuse_secret_key.get_secret_value()
    token = base64.b64encode(f"{settings.langfuse_public_key}:{secret}".encode()).decode()
    return OtlpTarget(
        "langfuse",
        f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
        {"Authorization": f"Basic {token}"},
    )


def setup_tracing(
    service_name: str,
    endpoint: str | None,
    exporter: SpanExporter | None = None,
    langfuse: OtlpTarget | None = None,
) -> None:
    global _tracer, _provider
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "service.version": __version__})
    )
    exporters.clear()
    if exporter is not None:
        # An injected exporter (tests) gets every span synchronously.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        exporters.append("test")
    targets: list[OtlpTarget] = []
    if endpoint and exporter is None:
        targets.append(OtlpTarget("jaeger", f"{endpoint.rstrip('/')}/v1/traces", {}))
    if langfuse is not None:
        targets.append(langfuse)
    if targets:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        for t in targets:  # one batch processor each: a slow Langfuse never delays Jaeger
            otlp = OTLPSpanExporter(endpoint=t.endpoint, headers=t.headers or None, timeout=3)
            provider.add_span_processor(BatchSpanProcessor(otlp, schedule_delay_millis=500))
            exporters.append(t.name)
    _provider = provider
    _tracer = provider.get_tracer("prahari.gateway", __version__)


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def tracer() -> trace.Tracer:
    if _tracer is None:
        setup_tracing("prahari-gateway", None)
    assert _tracer is not None
    return _tracer


def trace_id_of(span: Span) -> str:
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else ""


@contextmanager
def stage_span(parent: Span, stage: str, **attributes: Any) -> Iterator[Span]:
    """A child span of the request's root span for one pipeline stage."""
    ctx = trace.set_span_in_context(parent)
    with tracer().start_as_current_span(
        f"prahari.{stage}", context=ctx, attributes={"prahari.stage": stage, **_clean(attributes)}
    ) as span:
        yield span


def start_root(
    model: str, attributes: dict[str, Any], carrier: Mapping[str, str] | None = None
) -> Span:
    """The request's root span. With a W3C `traceparent` in `carrier` (the caller's headers) it
    joins the caller's trace, so one trace runs from the client app through the gateway to the
    model; without one it starts a new trace."""
    parent = propagate.extract(dict(carrier)) if carrier else otel_context.Context()
    return tracer().start_span(
        "POST /v1/chat/completions",
        kind=SpanKind.SERVER,
        attributes={
            "gen_ai.request.model": model,
            "langfuse.trace.name": f"chat {model}",
            # the request, not a model call: usage is counted once, on the generation below
            "langfuse.observation.type": "span",
            **_clean(attributes),
        },
        context=parent,
    )


def start_genai_span(
    parent: Span,
    backend: str,
    provider: str,
    upstream_model: str,
    params: dict[str, Any],
    hedge: bool = False,
) -> Span:
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": upstream_model,
        "prahari.backend": backend,
        "prahari.hedge": hedge,
        # Langfuse: this span is the model call (a "generation"), priced and counted there
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": upstream_model,
    }
    for key, attr in (
        ("temperature", "gen_ai.request.temperature"),
        ("top_p", "gen_ai.request.top_p"),
        ("max_tokens", "gen_ai.request.max_tokens"),
        ("seed", "gen_ai.request.seed"),
    ):
        if params.get(key) is not None:
            attrs[attr] = params[key]
    return tracer().start_span(
        f"chat {upstream_model}",
        kind=SpanKind.CLIENT,
        context=trace.set_span_in_context(parent),
        attributes=attrs,
    )


def end_ok(span: Span, **attributes: Any) -> None:
    span.set_attributes(_clean(attributes))
    span.set_status(Status(StatusCode.OK))
    span.end()


def end_error(span: Span, message: str, **attributes: Any) -> None:
    span.set_attributes(_clean({"error.type": message[:120], **attributes}))
    span.set_status(Status(StatusCode.ERROR, message[:200]))
    span.end()


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) for v in value]
        else:
            out[key] = str(value)
    return out
