"""Property-based tests: invariants checked over thousands of generated inputs.

The streaming output guard is the riskiest code in the request path (it rewrites what the user
sees, chunk by chunk), so its core promise is stated as a property: however the model's text is
split into chunks, the redacted stream equals redacting the whole text at once. Hypothesis
generates the texts (biased towards emails, phone and card numbers, secrets and separators) and
the chunkings, and shrinks any failure to a minimal example.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aegis.cache import build_keys
from aegis.guardrails.output import MAX_HOLD, StreamRedactor, redact_text
from aegis.retry import full_jitter

PIECES = st.sampled_from(
    [
        "vinay@example.com",
        "a.b+c@mail.co.in",
        "+91 98765 43210",
        "(415) 555-0123",
        "415-555-0123",
        "9876543210",
        "4111 1111 1111 1111",
        "4111 1111 1111 1112",
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX",
        "AKIAABCDEFGHIJKLMNOP",
        "pong0",
        "2026-11-05",
        "127.0.0.1:8000",
        "v3.12",
        "(1)",
        "x",
        "word",
        '"',
        ",",
        ";",
        "{",
        "}",
        "=",
        "&",
    ]
)
SEPARATORS = st.sampled_from([" ", "  ", "\n", "\t", "", ",", '":"', " - "])
TEXT = st.lists(st.tuples(PIECES, SEPARATORS), max_size=14).map(
    lambda parts: "".join(p + sep for p, sep in parts)
)


@st.composite
def chunked(draw: st.DrawFn) -> tuple[str, list[str]]:
    text = draw(TEXT)
    cuts = sorted(draw(st.sets(st.integers(0, len(text)), max_size=12)))
    pieces, last = [], 0
    for c in [*cuts, len(text)]:
        pieces.append(text[last:c])
        last = c
    return text, pieces


@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(chunked())
def test_streaming_redaction_equals_redacting_the_whole_text(case: tuple[str, list[str]]) -> None:
    text, pieces = case
    assert len(text) < MAX_HOLD * 4  # long unbroken runs are a separate, tested case
    r = StreamRedactor()
    streamed = "".join(r.feed(p) for p in pieces) + r.flush()
    assert streamed == redact_text(text)


@settings(max_examples=500, deadline=None)
@given(chunked())
def test_streaming_never_loses_or_reorders_text_without_values(case: tuple[str, list[str]]) -> None:
    """Text with nothing to redact passes through unchanged, in order, whatever the chunking."""
    text, pieces = case
    if redact_text(text) != text:
        return
    r = StreamRedactor()
    assert "".join(r.feed(p) for p in pieces) + r.flush() == text


@given(st.integers(0, 12), st.floats(0.01, 2.0), st.floats(0.01, 10.0))
def test_backoff_stays_inside_its_window(attempt: int, base: float, cap: float) -> None:
    delay = full_jitter(attempt, base, cap)
    assert 0.0 <= delay <= min(cap, base * 2**attempt) + 1e-9


MESSAGES = st.lists(
    st.fixed_dictionaries(
        {"role": st.sampled_from(["user", "assistant", "system"]), "content": st.text(max_size=40)}
    ),
    min_size=1,
    max_size=5,
)


@given(MESSAGES, st.sampled_from(["a", "b"]), st.floats(0, 1.5))
def test_cache_keys_separate_tenants_and_parameters(
    messages: list[dict[str, str]], tenant: str, temperature: float
) -> None:
    """The same conversation never shares a cache entry across tenants or temperatures."""
    k1 = build_keys(tenant, "m", messages, {"temperature": temperature}, 200)
    k2 = build_keys(tenant + "x", "m", messages, {"temperature": temperature}, 200)
    k3 = build_keys(tenant, "m", messages, {"temperature": temperature + 0.1}, 200)
    assert k1.exact != k2.exact and k1.partition != k2.partition
    assert k1.exact != k3.exact and k1.partition != k3.partition
    assert build_keys(tenant, "m", messages, {"temperature": temperature}, 200) == k1
