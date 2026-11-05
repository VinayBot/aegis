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

from prahari.guardrails.given_names import GIVEN_NAMES

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
# An address typed without its ending ("vinay@gmail") is still someone's address; a model would
# happily complete it. Matches user@word when no ".tld" follows (full addresses use the pattern above).
_EMAIL_NO_TLD_PATTERN = Pattern(
    name="email_without_tld",
    regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\b(?!\.[A-Za-z0-9-])",
    score=0.85,
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
# Software, model and site names that spaCy tags as people ("when Redis is down" -> PERSON,
# "runs on one Mac" -> PERSON). Questions about this gateway's own stack, and the assistant's
# doc excerpts, must reach the model intact.
_NOT_NAMES = frozenset(
    [
        "prahari",
        "api",
        "apple",
        "apps",
        "breaker",
        "breakers",
        "chaos",
        "chroma",
        "chromium",
        "colima",
        "dashboard",
        "detailed",
        "dev",
        "docker",
        "fastapi",
        "gateway",
        "gemma",
        "gib",
        "grafana",
        "html",
        "jaeger",
        "json",
        "kafka",
        "kb",
        "kubernetes",
        "lab",
        "langfuse",
        "litellm",
        "llama",
        "lua",
        "mac",
        "macbook",
        "macos",
        "mistral",
        "mlx",
        "nexus",
        "node",
        "ollama",
        "openai",
        "pii",
        "playground",
        "playwright",
        "presidio",
        "primary",
        "python",
        "qwen",
        "redis",
        "stack",
        "swagger",
        "uvicorn",
        "veritas",
        "vite",
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


# "my name is vinay": people introduce themselves in lower case, and the small spaCy model misses
# a lone first name even when capitalised. After an explicit naming phrase the next one or two
# words are the name. After a looser phrase ("i am", "this is") only a known given name counts,
# so "I am fine" and "this is great" stay untouched.
_INTRO_STRONG = r"my\s+name\s+is|my\s+name's|name\s*:|call\s+me|i\s+am\s+called|i'm\s+called"
_INTRO_LOOSE = r"i\s+am|i'm|im|this\s+is|it's|it\s+is"
_INTRO_RE = re.compile(
    rf"\b(?:(?P<strong>{_INTRO_STRONG})|(?P<loose>{_INTRO_LOOSE}))\s+"
    r"(?P<first>[A-Za-z][A-Za-z'-]+)(?:\s+(?P<last>[A-Za-z][A-Za-z'-]+))?",
    re.I,
)
_NOT_NAME_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "so",
        "not",
        "also",
        "just",
        "here",
        "there",
        "from",
        "in",
        "at",
        "of",
        "to",
        "for",
        "with",
        "by",
        "on",
        "is",
        "am",
        "are",
        "was",
        "be",
        "my",
        "your",
        "his",
        "her",
        "our",
        "their",
        "this",
        "that",
        "it",
        "its",
        "i",
        "im",
        "i'm",
        "me",
        "you",
        "he",
        "she",
        "we",
        "they",
        "please",
        "sir",
        "madam",
        "thanks",
        "thank",
        "hi",
        "hello",
        "hey",
        "btw",
        "ok",
        "okay",
        "sure",
        "really",
        "very",
        "too",
        "still",
        "now",
    ]
)
SELF_INTRO = "SelfIntroRecognizer"


class SelfIntroRecognizer(EntityRecognizer):
    def __init__(self) -> None:
        super().__init__(supported_entities=["PERSON"], name=SELF_INTRO)

    def load(self) -> None:
        return None

    def analyze(self, text: str, entities: list[str], nlp_artifacts: object = None) -> list:
        if "PERSON" not in entities:
            return []
        found = []
        for m in _INTRO_RE.finditer(text):
            first = m.group("first")
            if first.lower() in _NOT_NAME_WORDS or first.lower() in _NOT_NAMES:
                continue
            if m.group("loose") and first.lower() not in GIVEN_NAMES:
                continue
            end = m.end("first")
            last = m.group("last")
            if last and last.lower() not in _NOT_NAME_WORDS and last.lower() not in _NOT_NAMES:
                end = m.end("last")
            found.append(
                RecognizerResult(
                    "PERSON",
                    m.start("first"),
                    end,
                    0.95,
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: SELF_INTRO,
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                )
            )
        return found


def _plausible_name(span: str) -> bool:
    words = [w for w in re.split(r"[\s,]+", span.strip()) if w]
    if words and words[-1].lower().strip(".") in _ORGANISATION_WORDS:
        return False
    letters = [re.sub(r"[^a-z]", "", w.lower()) for w in words]
    if all(w in _NOT_NAMES for w in letters if w):  # a real name next to "Redis" stays redacted
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
        PatternRecognizer(
            supported_entity="EMAIL_ADDRESS", patterns=[_EMAIL_PATTERN, _EMAIL_NO_TLD_PATTERN]
        ),
        GivenNameRecognizer(),
        SelfIntroRecognizer(),
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


def _from(result: RecognizerResult, recognizer: str) -> bool:
    meta = result.recognition_metadata or {}
    return meta.get(RecognizerResult.RECOGNIZER_NAME_KEY) == recognizer


def redact(text: str) -> Redaction:
    if not text.strip():
        return Redaction(text, {})
    results = analyzer().analyze(text=text, language="en", entities=list(ALLOWED_ENTITIES))
    results = [
        r
        for r in results
        if r.score >= MIN_SCORE
        and (
            r.entity_type != "PERSON"
            or _from(r, SELF_INTRO)  # the person named themselves: case does not matter
            or _plausible_name(text[r.start : r.end])
        )
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
