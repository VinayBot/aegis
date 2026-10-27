"""PII redaction, adapted from Veritas (assignment3/backend/app/privacy/detector.py).

Kept from Veritas: Presidio with the small spaCy model, the narrowed entity list, the custom
recognizers Presidio lacks (Aadhaar, PAN, any-domain email, given-name + surname), and the
plausible-name filter that stops the NER model masking ordinary phrases. Changed for a gateway:
no vault and no reversible tokens; a value is replaced by a typed placeholder (<EMAIL_ADDRESS>)
before the prompt reaches the cache, the embedder or any model, and only counts are traced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider

from aegis.guardrails.given_names import GIVEN_NAMES

MIN_SCORE = 0.35

ALLOWED_ENTITIES = (
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "PERSON",
    "IP_ADDRESS",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_SSN",
    "AADHAAR",
    "PAN_INDIA",
)

_AADHAAR_PATTERN = Pattern(name="aadhaar", regex=r"\b\d{4}\s?\d{4}\s?\d{4}\b", score=0.55)
_PAN_INDIA_PATTERN = Pattern(name="pan_india", regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", score=0.6)
_EMAIL_PATTERN = Pattern(
    name="email_any_domain",
    regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b",
    score=0.9,
)
_GIVEN_NAME_RE = re.compile(r"\b(?=([A-Z][a-z]+)\s+([A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b)")
_NAME_PARTICLES = frozenset(
    [
        "de",
        "da",
        "di",
        "du",
        "del",
        "della",
        "der",
        "den",
        "van",
        "von",
        "la",
        "le",
        "al",
        "bin",
        "ibn",
        "y",
        "e",
    ]
)
_ORGANISATION_WORDS = frozenset(
    [
        "collective",
        "inc",
        "ltd",
        "llc",
        "plc",
        "corp",
        "corporation",
        "company",
        "co",
        "group",
        "holdings",
        "foundation",
        "university",
        "college",
        "institute",
        "bank",
        "partners",
        "systems",
        "technologies",
        "labs",
        "agency",
        "council",
        "committee",
        "department",
        "ministry",
        "association",
        "society",
        "trust",
        "gateway",
        "cache",
    ]
)


class GivenNameRecognizer(EntityRecognizer):
    def __init__(self) -> None:
        super().__init__(supported_entities=["PERSON"], name="GivenNameRecognizer")

    def load(self) -> None:
        return None

    def analyze(self, text: str, entities: list[str], nlp_artifacts: object = None) -> list:
        if "PERSON" not in entities:
            return []
        return [
            RecognizerResult("PERSON", m.start(1), m.end(2), 0.9)
            for m in _GIVEN_NAME_RE.finditer(text)
            if m.group(1).lower() in GIVEN_NAMES
        ]


def _plausible_name(span: str) -> bool:
    words = [w for w in re.split(r"[\s,]+", span.strip()) if w]
    if words and words[-1].lower().strip(".") in _ORGANISATION_WORDS:
        return False
    return bool(words) and all(
        w[0].isupper() or not w[0].isalpha() or w.lower() in _NAME_PARTICLES for w in words
    )


@lru_cache(maxsize=1)
def analyzer() -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
    for recognizer in (
        PatternRecognizer(supported_entity="EMAIL_ADDRESS", patterns=[_EMAIL_PATTERN]),
        GivenNameRecognizer(),
        PatternRecognizer(
            supported_entity="AADHAAR",
            patterns=[_AADHAAR_PATTERN],
            context=["aadhaar", "uidai", "aadhar"],
        ),
        PatternRecognizer(
            supported_entity="PAN_INDIA",
            patterns=[_PAN_INDIA_PATTERN],
            context=["pan", "income tax", "permanent account number"],
        ),
    ):
        engine.registry.add_recognizer(recognizer)
    return engine


@dataclass
class Redaction:
    text: str
    counts: dict[str, int]


def redact(text: str) -> Redaction:
    if not text.strip():
        return Redaction(text, {})
    results = analyzer().analyze(text=text, language="en", entities=list(ALLOWED_ENTITIES))
    results = [
        r
        for r in results
        if r.score >= MIN_SCORE
        and (r.entity_type != "PERSON" or _plausible_name(text[r.start : r.end]))
    ]
    # Longest span first at each start; drop anything overlapping a kept span.
    results.sort(key=lambda r: (r.start, -(r.end - r.start)))
    kept: list[RecognizerResult] = []
    last_end = -1
    for r in results:
        if r.start >= last_end:
            kept.append(r)
            last_end = r.end
    counts: dict[str, int] = {}
    out = text
    for r in sorted(kept, key=lambda r: r.start, reverse=True):
        out = out[: r.start] + f"<{r.entity_type}>" + out[r.end :]
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return Redaction(out, counts)
