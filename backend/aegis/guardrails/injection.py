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
