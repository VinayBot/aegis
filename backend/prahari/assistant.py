"""PRAHARI, the project assistant.

Grounded in the project's own docs (README, docs/*.md, DESIGN.md, the gateway YAML and the API
schema), chunked by heading and embedded with the same bge-small embedder the cache uses.
Live questions ("is the primary healthy?") are answered from the control plane's current state.

Every answer is a real request through the gateway's own /v1/chat/completions, over HTTP, with
its own API key: the assistant is a live demo of the product, and its trace chip shows what the
gateway did with it. When neither the docs nor live state cover a question it says so.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from prahari.state import AppState

API_KEY = "prahari-assistant-key"
MODEL = "prahari-chat"
MIN_SIMILARITY = 0.58
# Hybrid retrieval: BM25 keyword ranking fused with the dense ranking (reciprocal rank fusion,
# k=60, as in Veritas). A chunk is relevant when its embedding is close enough, or when it is a top
# keyword match on a rare term ("coalescer" is one word that dense search can rank below a
# document that merely discusses coalescing). Out-of-scope questions match neither, so the
# assistant still declines them.
RRF_K = 60
BM25_K1, BM25_B = 1.5, 0.75
# A keyword-only match needs both a strong BM25 score and some semantic closeness: keyword hits
# alone also fire on off-topic text that happens to share a word (measured: "capital of France"
# scores 8.4 against a report that quotes that test question).
BM25_STRONG = 2.5
DENSE_FLOOR = 0.5
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "does",
        "do",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
        "me",
        "my",
        "can",
        "should",
        "would",
        "will",
    ]
)


def _terms(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


NO_ANSWER = "The project docs don't cover that, so I won't guess."
SKIP_DOCS = frozenset({"DEMO_SCRIPT.md"})
RECENT_WINDOW_S = 600

LIVE_PATTERNS = re.compile(
    r"\b(right now|currently|current|live|is the|are the|healthy|health|status|hit rate|"
    r"last request|my last|just happened|what happened|failover|failed over|breaker|latency now|"
    r"how many requests|uptime|down\b|up\b)",
    re.I,
)


@dataclass
class Chunk:
    source: str
    heading: str
    text: str


def _split_markdown(source: str, text: str, max_chars: int = 1100) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading = source
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if len(body) > 40:
            for i in range(0, len(body), max_chars):
                chunks.append(Chunk(source, heading, body[i : i + max_chars]))
        buf.clear()

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or source
        buf.append(line)
    flush()
    return chunks


class DocsIndex:
    def __init__(self, state: AppState) -> None:
        self.s = state
        self.chunks: list[Chunk] = []
        self.vectors: np.ndarray | None = None
        self.built_at = 0.0

    def sources(self) -> list[Path]:
        root = self.s.settings.project_dir
        paths = [root / "README.md", root / "DESIGN.md", self.s.settings.config_path]
        # DEMO_SCRIPT.md is the presenter's script: it quotes the demo questions word for word,
        # so a small model asked one of them cites the script instead of answering.
        paths += sorted(p for p in self.s.settings.docs_dir.glob("*.md") if p.name not in SKIP_DOCS)
        return [p for p in paths if p.exists()]

    def build(self, openapi: dict[str, Any] | None = None) -> int:
        root = self.s.settings.project_dir
        chunks: list[Chunk] = []
        for path in self.sources():
            rel = str(path.relative_to(root))
            text = path.read_text(errors="replace")
            if path.suffix in (".yaml", ".yml"):
                chunks += [
                    Chunk(rel, "Gateway configuration (YAML)", text[i : i + 1100])
                    for i in range(0, len(text), 1100)
                ]
            else:
                chunks += _split_markdown(rel, text)
        # The YAML and the docs' tables are hard for a 3B model to read correctly, so the numbers
        # and failure behaviours that matter are also stated as plain sentences.
        # one chunk per fact: a long list of facts dilutes each one's embedding, so a specific
        # question ("which embedding model...?") would not find its line
        for line in settings_facts(self.s).splitlines():
            fact = line.lstrip("- ").strip()
            if fact:
                chunks.append(
                    Chunk("config/gateway.yaml", f"Gateway setting: {fact.split(':')[0]}", fact)
                )
        if openapi:
            lines = [
                f"{method.upper()} {path}: {op.get('summary') or op.get('description', '')}"
                for path, ops in openapi.get("paths", {}).items()
                for method, op in ops.items()
                if isinstance(op, dict)
            ]
            chunks.append(
                Chunk("API schema (/openapi.json)", "HTTP API endpoints", "\n".join(lines))
            )
        self.chunks = chunks
        texts = [f"{c.heading}\n{c.text}" for c in chunks]
        self.vectors = self.s.embedder.embed_sync(texts) if texts else None
        # BM25 statistics
        self._docs = [_terms(t) for t in texts]
        self._avgdl = (sum(map(len, self._docs)) / len(self._docs)) if self._docs else 0.0
        df: dict[str, int] = {}
        for doc in self._docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        n = len(self._docs)
        self._idf = {t: float(np.log(1 + (n - f + 0.5) / (f + 0.5))) for t, f in df.items()}
        self.built_at = time.time()
        return len(chunks)

    def bm25(self, query: str) -> np.ndarray:
        q = _terms(query)
        scores = np.zeros(len(self._docs), dtype=np.float32)
        for i, doc in enumerate(self._docs):
            if not doc:
                continue
            tf: dict[str, int] = {}
            for t in doc:
                tf[t] = tf.get(t, 0) + 1
            norm = BM25_K1 * (1 - BM25_B + BM25_B * len(doc) / (self._avgdl or 1))
            scores[i] = sum(
                self._idf.get(t, 0.0) * tf[t] * (BM25_K1 + 1) / (tf[t] + norm) for t in q if t in tf
            )
        return scores

    async def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float, bool]]:
        """Top k chunks by reciprocal rank fusion of dense and BM25 rankings, each with its dense
        similarity and whether it is relevant enough to ground an answer."""
        if self.vectors is None or not self.chunks:
            return []
        q = await self.s.embedder.embed_one(query)
        sims = self.vectors @ q
        keyword = self.bm25(query)
        dense_rank = {int(i): r for r, i in enumerate(np.argsort(-sims))}
        bm25_rank = {int(i): r for r, i in enumerate(np.argsort(-keyword)) if keyword[i] > 0}
        fused = {
            i: 1 / (RRF_K + dense_rank[i]) + (1 / (RRF_K + bm25_rank[i]) if i in bm25_rank else 0)
            for i in range(len(self.chunks))
        }
        order = sorted(fused, key=lambda i: fused[i], reverse=True)[:k]
        return [
            (
                self.chunks[i],
                float(sims[i]),
                float(sims[i]) >= MIN_SIMILARITY
                or (
                    bm25_rank.get(i, 99) < 2
                    and float(keyword[i]) >= BM25_STRONG
                    and float(sims[i]) >= DENSE_FLOOR
                ),
            )
            for i in order
        ]


def settings_facts(state: AppState) -> str:
    cfg = state.config.get()
    br, ro, ca = cfg.breaker, cfg.router, cfg.cache
    return "\n".join(
        [
            f"- Circuit breaker: it opens after {br.error_threshold} consecutive errors, or at once on "
            f"a first-token timeout. Once open it stays open for {br.open_seconds:g} seconds, and traffic "
            "skips that backend. Then it is half-open: exactly one live request probes it; success "
            "closes the breaker, failure opens it again.",
            f"- First-token timeout: {ro.first_token_timeout_seconds:g} seconds plus 1 second per 1,000 "
            "prompt tokens.",
            f"- Hedging: after the primary's p95 time to first token (never sooner than "
            f"{ro.hedge_min_ms:g} ms; {ro.hedge_default_ms:g} ms before there is enough history) the "
            "request is also sent to a free backend; the first to stream wins.",
            f"- Busy backend: wait up to {ro.slot_wait_seconds:g} seconds for a free slot, then try the "
            f"next backend; nothing free for {ro.queue_deadline_seconds:g} seconds returns 503.",
            f"- Semantic cache: a cosine similarity of at least {ca.semantic_threshold:g} is a hit; only "
            f"last messages under {ca.semantic_max_tokens} tokens are looked up; entries live "
            f"{ca.ttl_seconds // 3600} hours; a lookup slower than {ca.embed_budget_ms} ms is skipped.",
            (
                f"- Embeddings for the semantic cache: {state.settings.embedder_model} (bge-small, "
                "384 dimensions), computed on the CPU inside the gateway process."
            ),
            (
                "- Coalescer: identical requests in flight at the same moment share one upstream "
                "call; the first is the leader, the others follow its stream."
            ),
            "- If Redis is down: the exact and semantic caches are skipped, rate limits fall back to "
            "an in-process token bucket, and health shows degraded; requests are still answered.",
            "- If a backend dies mid-stream: the client gets an SSE error event, tokens are estimated "
            "(plus or minus 30%), and nothing is cached.",
            "- If the whole gateway is down: the website plays recorded real traffic under a "
            "'REPLAY: backend offline' banner.",
        ]
    )


def _roles(state: AppState) -> dict[str, str]:
    models = state.config.get().models
    chain = models[MODEL].chain if MODEL in models else []
    return {b: ("primary" if i == 0 else "fallback") for i, b in enumerate(chain)}


def _chaos_sentence(entry: dict[str, Any], who: str) -> str:
    action = str(entry.get("action", ""))
    auto = action.startswith("auto-revert ")
    action = action.removeprefix("auto-revert ")
    by = "automatically" if auto else "by the Chaos Lab"
    if action == "kill":
        how = (
            "froze its process (SIGSTOP)"
            if entry.get("mode") == "sigstop"
            else ("made it refuse connections")
        )
        return f"the Chaos Lab killed {who}: it {how}, so it stopped answering"
    if action == "clear kill":
        return f"{who} was revived {by}"
    if action == "latency":
        return f"the Chaos Lab added {entry.get('value')} ms of latency to {who}"
    if action == "clear latency":
        return f"the injected latency on {who} was removed {by}"
    if action == "error_rate":
        return f"the Chaos Lab made {who} fail {float(entry.get('value') or 0):.0%} of requests"
    if action == "clear error_rate":
        return f"the injected errors on {who} were removed {by}"
    return f"chaos action '{action}' on {who}"


def _breaker_sentence(entry: dict[str, Any], who: str, open_s: float) -> str:
    state, reason = entry.get("state"), str(entry.get("reason") or "")
    if state == "open":
        return (
            f"the circuit breaker on {who} opened ({reason}); traffic skipped it for "
            f"{open_s:g} seconds and went to the next backend"
        )
    if state == "half_open":
        return f"the cool-down on {who} ended; its breaker went half-open, waiting for one probe"
    if state == "closed" and "manual" in reason:
        return f"the breaker on {who} was reset by hand and closed"
    if state == "closed":
        return f"a probe request to {who} succeeded and its breaker closed again: it is healthy"
    return f"the breaker on {who} became {state}"


def recent_events(state: AppState, window_s: float = RECENT_WINDOW_S) -> list[str]:
    """Chaos actions and breaker changes in the last few minutes, oldest first, as sentences.

    A 3B model cannot reliably assemble a story from raw event rows; it can retell a timeline.
    """
    roles = _roles(state)

    def who(backend: str) -> str:
        role = roles.get(backend)
        return f"the {role} ({backend})" if role else backend

    since = time.time() - window_s
    open_s = state.config.get().breaker.open_seconds
    items: list[tuple[float, str]] = []
    for e in list(state.chaos.log):
        if e.get("ts", 0) >= since:
            items.append((e["ts"], _chaos_sentence(e, who(str(e.get("backend"))))))
    for b in list(state.metrics.breaker_history):
        if b.get("ts", 0) >= since:
            items.append((b["ts"], _breaker_sentence(b, who(str(b.get("backend"))), open_s)))
    items.sort(key=lambda it: it[0])
    return [f"- {time.strftime('%H:%M:%S', time.localtime(ts))}: {text}." for ts, text in items]


def live_facts(state: AppState) -> str:
    events = recent_events(state)
    minutes = RECENT_WINDOW_S // 60
    lines = [f"What happened in the last {minutes} minutes, oldest first:"]
    lines += events or [f"- Nothing: no chaos actions and no breaker changes in {minutes} minutes."]
    lines.append("Current state:")
    roles = {f"backend:{b}": r for b, r in _roles(state).items()}
    for name, h in sorted(state.health.items()):
        extra = f", breaker {h['breaker']}" if "breaker" in h else ""
        role = f" (the {roles[name]})" if name in roles else ""
        lines.append(f"- {name}{role}: {h['status']}{extra} ({h['detail'][:90]})")
    k = state.metrics.kpis(300)["current"]
    hit = "n/a" if k["hit_rate"] is None else f"{k['hit_rate'] * 100:.0f}%"
    lines.append(
        f"- last 5 min: {k['requests']} requests, cache hit rate {hit}, "
        f"TTFT p95 {k['ttft_p95_ms'] or 'n/a'} ms"
    )
    recent = [r for r in state.metrics.recent_requests if r.get("tenant") != "prahari-bot"][:3]
    for r in recent:
        lines.append(
            f"- request {r['request_id']} ({r['tenant']}): {r['status']}, cache {r['cache']}, "
            f"backend {r['backend']}, failovers {r['failovers']}, hedged {bool(r['hedged'])}, "
            f"latency {r['latency_ms']} ms" + (f", error {r['error']}" if r.get("error") else "")
        )
    lines += ["Settings:", settings_facts(state)]
    return "\n".join(lines)


SYSTEM = """You are PRAHARI, the assistant for the PRAHARI AI gateway project.
Answer only from the CONTEXT below. Cite sources inline as [1], [2] matching the numbered context.
Live state lines are marked [live]. Copy every number, limit and duration exactly as the CONTEXT states it;
never round or substitute one. Be concise: at most 5 sentences or a short list.
If the context does not contain the answer, reply exactly: "{no_answer}"
"""


async def _retrieve(state: AppState, question: str) -> tuple[list[dict[str, Any]], str, bool]:
    """The cited sources, the numbered context for the model, and whether live state was added
    (an empty context means the question is not grounded: the assistant declines)."""
    index: DocsIndex = state.docs_index  # type: ignore[attr-defined]
    hits = await index.search(question, k=4)
    relevant = [(c, sim) for c, sim, ok in hits if ok]
    wants_live = bool(LIVE_PATTERNS.search(question))
    sources: list[dict[str, Any]] = [
        {"n": i + 1, "source": c.source, "heading": c.heading, "score": round(sim, 3)}
        for i, (c, sim) in enumerate(relevant)
    ]
    context = "\n\n".join(
        f"[{i + 1}] ({c.source} > {c.heading})\n{c.text}" for i, (c, _) in enumerate(relevant)
    )
    if wants_live:
        context += "\n\n[live] Current gateway state:\n" + live_facts(state)
        sources.append(
            {
                "n": "live",
                "source": "control plane",
                "heading": "/api/health, /api/metrics",
                "score": None,
            }
        )
    return sources, context.strip(), wants_live


async def answer(
    state: AppState, question: str, history: list[dict[str, str]], no_cache: bool = False
) -> AsyncIterator[dict[str, Any]]:
    sources, context, wants_live = await _retrieve(state, question)
    grounded = bool(context)
    yield {"type": "sources", "sources": sources, "grounded": grounded}

    if grounded:
        messages = [
            {"role": "system", "content": SYSTEM.format(no_answer=NO_ANSWER)},
            *history[-4:],
            {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}"},
        ]
    else:
        # Nothing to ground on: still routed through the gateway, but the model is told the
        # exact refusal so a small model cannot improvise one.
        messages = [
            {"role": "system", "content": "Reply with exactly the sentence the user gives."},
            {"role": "user", "content": NO_ANSWER},
        ]

    headers = {
        "authorization": f"Bearer {API_KEY}",
        "x-prahari-summary": "1",
        "x-prahari-client": "assistant",
    }
    if no_cache or wants_live:
        headers["cache-control"] = "no-cache"  # live answers must not be replayed later
    body = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "temperature": 0,
        "max_tokens": 300,
    }
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=2.0)) as client,
        client.stream(
            "POST", f"{state.settings.self_url}/v1/chat/completions", json=body, headers=headers
        ) as resp,
    ):
        meta = {k: v for k, v in resp.headers.items() if k.startswith("x-")}
        yield {"type": "meta", "status": resp.status_code, "headers": meta}
        if resp.status_code != 200:
            payload = json.loads((await resp.aread()) or b"{}")
            yield {"type": "error", "status": resp.status_code, "error": payload.get("error")}
            return
        event = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            if event == "prahari":
                yield {"type": "summary", "summary": obj}
            elif event == "error":
                yield {"type": "error", "status": 200, "error": obj.get("error")}
            else:
                for choice in obj.get("choices") or []:
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield {"type": "token", "text": text}
            event = None
