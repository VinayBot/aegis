"""OpenTelemetry: every request is one trace, every pipeline stage one span, exported over OTLP
HTTP (Jaeger here; any OTLP collector, Langfuse included, can ingest the same export).

The upstream model call follows the GenAI semantic conventions: span name `chat {model}`,
`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.*`, `gen_ai.response.*` and
`gen_ai.usage.*`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from aegis import __version__

_tracer: trace.Tracer | None = None
_provider: TracerProvider | None = None


def setup_tracing(
    service_name: str, endpoint: str | None, exporter: SpanExporter | None = None
) -> None:
    global _tracer, _provider
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "service.version": __version__})
    )
    if exporter is not None:
        # An injected exporter (tests) gets every span synchronously.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces", timeout=2)
        provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=500))
    _provider = provider
    _tracer = provider.get_tracer("aegis.gateway", __version__)


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def tracer() -> trace.Tracer:
    if _tracer is None:
        setup_tracing("aegis-gateway", None)
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
        f"aegis.{stage}", context=ctx, attributes={"aegis.stage": stage, **_clean(attributes)}
    ) as span:
        yield span


def start_root(model: str, attributes: dict[str, Any]) -> Span:
    return tracer().start_span(
        "POST /v1/chat/completions",
        kind=SpanKind.SERVER,
        attributes={"gen_ai.request.model": model, **_clean(attributes)},
        context=otel_context.Context(),
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
        "aegis.backend": backend,
        "aegis.hedge": hedge,
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
