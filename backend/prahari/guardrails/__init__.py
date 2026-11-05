"""Input guardrails: PII redaction and the prompt-injection check, applied to user messages."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from prahari.guardrails import injection, pii
from prahari.tokens import message_text


@dataclass
class GuardrailOutcome:
    messages: list[dict[str, Any]]
    redactions: dict[str, int] = field(default_factory=dict)
    blocked: bool = False
    reason: str | None = None
    matched: str | None = None


def _check(messages: list[dict[str, Any]]) -> GuardrailOutcome:
    out: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    for message in messages:
        if message.get("role") != "user":
            out.append(message)
            continue
        text = message_text(message)
        verdict = injection.scan(text)
        if verdict.is_injection:
            return GuardrailOutcome(
                messages, blocked=True, reason="prompt_injection", matched=verdict.matched
            )
        redaction = pii.redact(text)
        for entity, n in redaction.counts.items():
            totals[entity] = totals.get(entity, 0) + n
        out.append({**message, "content": redaction.text} if redaction.counts else message)
    return GuardrailOutcome(out, redactions=totals)


async def check(messages: list[dict[str, Any]]) -> GuardrailOutcome:
    # Presidio + spaCy are CPU-bound; keep them off the event loop.
    return await asyncio.to_thread(_check, messages)


def warm() -> None:
    pii.analyzer()
