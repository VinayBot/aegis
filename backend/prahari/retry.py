"""Exponential backoff with full jitter (the AWS Architecture Blog variant).

delay(n) = uniform(0, min(cap, base * 2**n)). Full jitter spreads retries from many clients over the
whole window, so a backend that blipped is not hit by a synchronised wave when it comes back.
"""

from __future__ import annotations

import random


def full_jitter(
    attempt: int, base_s: float, cap_s: float, rng: random.Random | None = None
) -> float:
    ceiling = min(cap_s, base_s * (2**attempt))
    return (rng or random).uniform(0.0, max(0.0, ceiling))
