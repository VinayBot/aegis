"""User feedback on answers, as Langfuse scores.

A thumbs up or down on an answer is stored in the ledger, counted in Prometheus
(`aegis_feedback_total`) and, when Langfuse keys are set, attached to that request's trace as a
`user-feedback` score through Langfuse's public scores API, so quality can be sliced by model,
backend, cache outcome or tenant next to latency and cost.
"""

from __future__ import annotations

import logging

import httpx

from aegis.settings import Settings
from aegis.telemetry import langfuse_target

log = logging.getLogger("aegis.feedback")
SCORE_NAME = "user-feedback"


def score_payload(trace_id: str, value: int, comment: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "traceId": trace_id,
        "name": SCORE_NAME,
        "value": value,
        "dataType": "BOOLEAN",
    }
    if comment:
        payload["comment"] = comment
    return payload


async def push_langfuse_score(
    settings: Settings, trace_id: str, value: int, comment: str | None
) -> bool:
    target = langfuse_target(settings)
    if target is None or not trace_id:
        return False
    url = f"{settings.langfuse_host.rstrip('/')}/api/public/scores"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url, json=score_payload(trace_id, value, comment), headers=target.headers
            )
        resp.raise_for_status()
        return True
    except Exception as exc:  # feedback must never fail because Langfuse is away
        log.warning("Langfuse score not sent", extra={"error": f"{type(exc).__name__}: {exc}"})
        return False
