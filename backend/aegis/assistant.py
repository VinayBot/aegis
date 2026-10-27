"""AEGIS, the project assistant.

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

from aegis.state import AppState

API_KEY = "aegis-assistant-key"
MIN_SIMILARITY = 0.58
NO_ANSWER = "The project docs don't cover that, so I won't guess."

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
        paths += sorted(self.s.settings.docs_dir.glob("*.md"))
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
        self.built_at = time.time()
        return len(chunks)

    async def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        if self.vectors is None or not self.chunks:
            return []
        q = await self.s.embedder.embed_one(query)
        sims = self.vectors @ q
        order = np.argsort(-sims)[:k]
        return [(self.chunks[i], float(sims[i])) for i in order]


def live_facts(state: AppState) -> str:
    lines = []
    for name, h in sorted(state.health.items()):
        extra = f", breaker {h['breaker']}" if "breaker" in h else ""
        lines.append(f"- {name}: {h['status']}{extra} ({h['detail'][:90]})")
    k = state.metrics.kpis(300)["current"]
    hit = "n/a" if k["hit_rate"] is None else f"{k['hit_rate'] * 100:.0f}%"
    lines.append(
        f"- last 5 min: {k['requests']} requests, cache hit rate {hit}, "
        f"TTFT p95 {k['ttft_p95_ms'] or 'n/a'} ms"
    )
    for b in list(state.metrics.breaker_history)[-4:]:
        lines.append(
            f"- breaker event {time.strftime('%H:%M:%S', time.localtime(b['ts']))}: "
            f"{b['backend']} -> {b['state']} ({b['reason']})"
        )
    recent = [r for r in state.metrics.recent_requests if r.get("tenant") != "aegis-bot"][:3]
    for r in recent:
        lines.append(
            f"- request {r['request_id']} ({r['tenant']}): {r['status']}, cache {r['cache']}, "
            f"backend {r['backend']}, failovers {r['failovers']}, hedged {bool(r['hedged'])}, "
            f"latency {r['latency_ms']} ms" + (f", error {r['error']}" if r.get("error") else "")
        )
    return "\n".join(lines)


SYSTEM = """You are AEGIS, the assistant for the AEGIS AI gateway project.
Answer only from the CONTEXT below. Cite sources inline as [1], [2] matching the numbered context.
Live state lines are marked [live]. Be concise: at most 5 sentences or a short list.
If the context does not contain the answer, reply exactly: "{no_answer}"
"""


async def answer(
    state: AppState, question: str, history: list[dict[str, str]], no_cache: bool = False
) -> AsyncIterator[dict[str, Any]]:
    index: DocsIndex = state.docs_index  # type: ignore[attr-defined]
    hits = await index.search(question, k=4)
    relevant = [(c, s) for c, s in hits if s >= MIN_SIMILARITY]
    wants_live = bool(LIVE_PATTERNS.search(question))
    sources = [
        {"n": i + 1, "source": c.source, "heading": c.heading, "score": round(s, 3)}
        for i, (c, s) in enumerate(relevant)
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
    grounded = bool(relevant) or wants_live
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
        "x-aegis-summary": "1",
        "x-aegis-client": "assistant",
    }
    if no_cache or wants_live:
        headers["cache-control"] = "no-cache"  # live answers must not be replayed later
    body = {
        "model": "aegis-chat",
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
            if event == "aegis":
                yield {"type": "summary", "summary": obj}
            elif event == "error":
                yield {"type": "error", "status": 200, "error": obj.get("error")}
            else:
                for choice in obj.get("choices") or []:
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield {"type": "token", "text": text}
            event = None
