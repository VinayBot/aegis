"""Output guardrail: redact personal data and secrets in what the model writes, while it streams.

Input redaction (Presidio) stops a prompt's PII reaching a model. A model can still *produce*
PII: an address or phone number it memorised, a key pasted earlier in a long context, a card
number it invents. This scanner runs on every streamed token.

Streaming is the hard part: a value can be split across chunks ("vinay@" then "gmail.com"). So
text is held back from the last whitespace onward and released only once the word is complete,
and a trailing run of number-like words is held too (a phone or card number spans words); a
value is always seen whole. The hold-back is one word, so the client sees no visible delay. A
run of text with no whitespace is released once it passes MAX_HOLD characters, so a long code
block cannot stall the stream.

Regexes, not NER: Presidio costs milliseconds per call and would run hundreds of times per
answer. These patterns are the high-confidence, structured kinds (emails, phone numbers,
Luhn-valid card numbers, well-known secret formats), where a regex is precise.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# The longest structured value we redact is a fine-grained GitHub token (~93 chars); a run of
# text with no break longer than this is released (a 256-char token is not one of our values).
MAX_HOLD = 256
# Characters that can never be inside an email, phone, card or secret: a value always ends at
# one, so text up to the last of them can be released safely (minified JSON, URLs, CSV).
_BREAKS = frozenset(" \t\n\"'`,;{}[]<>|=&?")
_NUMERIC_CHARS = frozenset("+()-.0123456789")


def _glued(ready: str, pos: int) -> bool:
    """Is the text at `pos` stuck to a token on its left (no space between)?"""
    return pos > 0 and (ready[pos - 1].isalnum() or ready[pos - 1] in "_@%")


def _numeric_tail_start(ready: str) -> int | None:
    """Where to hold back from so no phone or card number is split: the trailing run of number
    characters and spaces ("+91 98765 43210 "); when that run is glued to a token
    ("Tel:+91 ...", "mail@x.com+91 ...", "43210v3.12", a key ending in digits) the whole token is
    held, and the scan goes on from its start, which may itself follow a number. None when the
    tail holds no digit. A plain backwards scan: linear, and each step moves strictly left, so no
    input can make it slow (a nested-quantifier regex here once backtracked exponentially)."""
    pos, digit = len(ready), False
    while True:
        end = pos
        while pos > 0 and (ready[pos - 1] in _NUMERIC_CHARS or ready[pos - 1] in " \t"):
            pos -= 1
        while pos < end and ready[pos] in " \t":  # spaces before a number are not glue
            pos += 1
        digit = digit or any(c.isdigit() for c in ready[pos:end])
        if not digit or not _glued(ready, pos):
            break
        while pos > 0 and not ready[pos - 1].isspace() and ready[pos - 1] not in _BREAKS:
            pos -= 1  # hold the whole token the number is stuck to
    return pos if digit and pos < len(ready) else None


def _luhn(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


# (entity, pattern, extra check)
_RULES: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = [
    (
        "SECRET",
        re.compile(
            r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}"
            r"|github_pat_[A-Za-z0-9_]{40,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})"
        ),
        None,
    ),
    (
        "EMAIL_ADDRESS",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
        None,
    ),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _luhn),
    (
        # unambiguous formats only, never inside a longer run of digits and never across a line:
        # an international number with its +, US (415) 555-0123 or 415-555-0123, Indian mobiles
        "PHONE_NUMBER",
        re.compile(
            r"(?<![\w+])\+\d{1,3}(?:[ .-]?\d{2,5}){2,4}(?![\d])"
            r"|(?<![\w(])\(\d{3}\) ?\d{3}[ .-]\d{4}\b"
            r"|(?<![\w-])\d{3}-\d{3}-\d{4}\b"
            r"|(?<![\w])[6-9]\d{9}\b"
        ),
        None,
    ),
]


def redact_text(text: str, counts: dict[str, int] | None = None) -> str:
    counts = counts if counts is not None else {}
    for entity, pattern, check in _RULES:

        def sub(
            m: re.Match[str],
            entity: str = entity,
            check: Callable[[str], bool] | None = check,
        ) -> str:
            if check is not None and not check(m.group(0)):
                return m.group(0)
            counts[entity] = counts.get(entity, 0) + 1
            return f"<{entity}>"

        text = pattern.sub(sub, text)
    return text


class StreamRedactor:
    """Feed streamed text in; get redacted text out, never splitting a value across chunks."""

    def __init__(self) -> None:
        self._held = ""
        self.counts: dict[str, int] = {}

    def feed(self, text: str) -> str:
        if not text:
            return ""
        buf = self._held + text
        cut = len(buf) - 1
        while cut >= 0 and buf[cut] not in _BREAKS:
            cut -= 1
        if cut < 0 and len(buf) <= MAX_HOLD:
            self._held = buf
            return ""
        if cut < 0:
            ready, self._held = buf, ""
        else:
            ready, self._held = buf[: cut + 1], buf[cut + 1 :]
            # a phone or card number spans words ("+91 98765 43210"): keep trailing number-like
            # words back too, until a word that cannot be part of a number arrives
            start = _numeric_tail_start(ready)
            if start is not None and len(self._held) + len(ready) - start <= MAX_HOLD:
                ready, self._held = ready[:start], ready[start:] + self._held
        return redact_text(ready, self.counts)

    def flush(self) -> str:
        out, self._held = redact_text(self._held, self.counts), ""
        return out

    @property
    def total(self) -> int:
        return sum(self.counts.values())
