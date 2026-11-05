"""Prompt-injection check, adapted from Veritas (assignment3/backend/app/guardrails/injection.py).

Kept: the regex heuristics, including the persona-override pattern and the own-record exemption.
Dropped: the DeBERTa classifier. It needs torch (~1.5 GB resident), which does not fit next to two
local models on a 16 GB Mac; see docs/DECISIONS.md. The regex layer catches the overt framings
the demo exercises; subtler attacks are a documented gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS = [
    re.compile(r"ignore (all )?(the )?(previous|prior|above) instructions", re.I),
    re.compile(r"disregard (all )?(the )?(previous|prior|above)", re.I),
    re.compile(r"new instructions?\s*:", re.I),
    re.compile(r"reveal (your|the) (system )?prompt", re.I),
    re.compile(r"print (your|the) (system )?(instructions|prompt)", re.I),
    re.compile(r"act as (if )?(you are )?(an? )?(unrestricted|jailbroken)\b", re.I),
    re.compile(r"</?(system|assistant|user)>", re.I),
    re.compile(r"\bDAN\b"),
    # families found missing by the eval (evals/data/injection.jsonl): forgetting or ignoring the
    # rules in other words, and asking to override the safety configuration
    re.compile(
        r"\b(forget|ignore|drop|abandon)\b[^.?!]{0,30}\b(your|the|all|any|previous|prior|earlier)"
        r"\b[^.?!]{0,20}\b(rules|instructions|guidelines|directives|constraints)\b",
        re.I,
    ),
    re.compile(
        r"\boverride\b[^.?!]{0,20}\b(safety|content|system|your)\b[^.?!]{0,20}"
        r"\b(settings|rules|filters|guardrails|restrictions|policies|instructions)\b",
        re.I,
    ),
    re.compile(
        r"\b(answer|respond|reply|comply)\b[^.?!]{0,20}\bwithout (any )?(restrictions|rules|filters|limits)\b",
        re.I,
    ),
    re.compile(r"you are now (in )?(developer|dan|unfiltered) mode", re.I),
    re.compile(
        r"\b(pretend|act|role-?play|imagine|behave)\b[^.?!]{0,60}\b("
        r"unfiltered|uncensored|unrestricted|jailbroken|"
        r"(without|with no|no) (any )?(rules|restrictions|filters|limits|guidelines|ethics)"
        r")\b",
        re.I,
    ),
]


@dataclass
class InjectionVerdict:
    is_injection: bool
    score: float
    matched: str | None = None


def scan(text: str) -> InjectionVerdict:
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return InjectionVerdict(True, 0.95, match.group(0))
    return InjectionVerdict(False, 0.0)
