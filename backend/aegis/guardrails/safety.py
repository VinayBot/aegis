"""Content safety with Llama Guard 3 (1B, served by Ollama): input and output.

Input: on a cache miss, the coalescing leader's prompt is classified before any generation model
sees it. Unsafe -> 400 `unsafe_content` with the hazard categories (S1-S14). Cache hits skip the
check (their prompt, or a near-identical one, already passed it), and coalesced followers share
the leader's verdict, so ten identical requests cost one classification.

Output: after a successful answer, the (prompt, answer) pair is classified in the background,
before the answer may enter the cache. An unsafe answer is never cached and is flagged in the
trace and the event stream. The client never waits for this check: tokens already streamed
cannot be recalled, so the output check protects every *later* request instead.

Threshold, not the bare label: Llama Guard 3 1B labels developer phrasing such as "how do I kill a
Python process?" unsafe, but with low confidence. Its own probability of the `unsafe` token
separates the cases cleanly (measured here: false positives 0.50-0.69, real hazards 0.986-1.0), so a
prompt is blocked only when P(unsafe) >= `safety.unsafe_threshold` (0.9). A borderline verdict is
allowed and recorded in the trace with its probability, the method the Llama Guard paper suggests.
When a server returns no token probabilities, the label alone decides.

Failure policy (`safety.on_error`): `allow` (default) lets the request through when the
classifier is slow or down and records `safety.skipped` in the trace; `block` refuses it (503).
Availability over strictness is the default for a gateway; flip it for regulated tenants.

Measured here: about 110-150 ms per input check once warm (2 s on a cold model load).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from aegis.tokens import message_text

# Llama Guard 3 hazard taxonomy (MLCommons)
CATEGORIES = {
    "S1": "violent crimes",
    "S2": "non-violent crimes",
    "S3": "sex-related crimes",
    "S4": "child sexual exploitation",
    "S5": "defamation",
    "S6": "specialized advice",
    "S7": "privacy",
    "S8": "intellectual property",
    "S9": "indiscriminate weapons",
    "S10": "hate",
    "S11": "suicide and self-harm",
    "S12": "sexual content",
    "S13": "elections",
    "S14": "code interpreter abuse",
}


@dataclass
class Verdict:
    safe: bool
    categories: list[str] = field(default_factory=list)
    ms: float = 0.0
    error: str | None = None  # set when the classifier could not answer
    p_unsafe: float | None = None  # the classifier's own probability of "unsafe", when known
    borderline: bool = False  # labelled unsafe, but below the threshold: allowed and recorded

    @property
    def labels(self) -> list[str]:
        return [f"{c} {CATEGORIES.get(c, '?')}" for c in self.categories]


def parse(text: str) -> tuple[bool, list[str]]:
    """Llama Guard answers `safe`, or `unsafe` then a line of comma-separated category codes."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty classifier output")
    head = lines[0].lower()
    if head == "safe":
        return True, []
    if head == "unsafe":
        cats = [
            c.strip().upper() for c in (lines[1] if len(lines) > 1 else "").split(",") if c.strip()
        ]
        return False, cats
    raise ValueError(f"unexpected classifier output: {lines[0][:40]!r}")


def p_unsafe_of(choice: dict[str, Any]) -> float | None:
    """P(first token = "unsafe") from OpenAI-style logprobs, or None if the server sent none."""
    content = (choice.get("logprobs") or {}).get("content") or []
    if not content:
        return None
    tops = content[0].get("top_logprobs") or [content[0]]
    probs: dict[str, float] = {}
    for t in tops:
        token = str(t.get("token", "")).strip().lower()
        probs[token] = probs.get(token, 0.0) + math.exp(float(t.get("logprob", -1e9)))
    if "unsafe" in probs or "safe" in probs:
        unsafe, safe = probs.get("unsafe", 0.0), probs.get("safe", 0.0)
        return unsafe / (unsafe + safe) if unsafe + safe > 0 else None
    return None


def _conversation(messages: list[dict[str, Any]], max_chars: int = 6000) -> list[dict[str, str]]:
    """Llama Guard takes alternating user/assistant turns. Everything the client controls is
    classified: system, developer and tool messages are folded into the user turn beside them,
    so a hazardous instruction cannot hide in a system prompt. The tail is kept when long."""
    turns: list[dict[str, str]] = []
    for m in messages:
        text = message_text(m)
        if not text:
            continue
        role = "assistant" if m.get("role") == "assistant" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": text})
    out: list[dict[str, str]] = []
    total = 0
    for turn in reversed(turns):
        total += len(turn["content"])
        if total > max_chars and out:
            break
        out.append(turn)
    out.reverse()
    while out and out[0]["role"] != "user":  # must start with the user
        out.pop(0)
    return out


class SafetyClassifier:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=1.0, read=5.0, write=2.0, pool=1.0)
        )

    async def classify(
        self,
        base_url: str,
        model: str,
        messages: list[dict[str, Any]],
        timeout_s: float,
        threshold: float = 0.9,
    ) -> Verdict:
        start = time.perf_counter()
        turns = _conversation(messages)
        if not turns:
            return Verdict(True, ms=0.0)
        try:
            resp = await self._client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "messages": turns,
                    "temperature": 0,
                    "max_tokens": 12,
                    "logprobs": True,
                    "top_logprobs": 5,
                },
                timeout=timeout_s,
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            safe, cats = parse(choice["message"]["content"] or "")
            p = p_unsafe_of(choice)
            ms = (time.perf_counter() - start) * 1000
            if not safe and p is not None and p < threshold:
                return Verdict(True, cats, ms=ms, p_unsafe=p, borderline=True)
            return Verdict(safe, cats, ms=ms, p_unsafe=p)
        except Exception as exc:
            return Verdict(
                True,
                ms=(time.perf_counter() - start) * 1000,
                error=f"{type(exc).__name__}: {exc}"[:160],
            )

    async def aclose(self) -> None:
        await self._client.aclose()
