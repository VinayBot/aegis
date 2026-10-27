"""Token estimates for budgeting before a request runs, and for billing when a backend omits
`usage`. Upstream usage always wins; an estimate is flagged so the UI can mark it ±30%."""

from __future__ import annotations

from typing import Any

ESTIMATE_MARGIN = 0.30


def estimate_text(text: str) -> int:
    # ~4 characters per token for English on BPE tokenizers; never zero for non-empty text.
    return max(1, (len(text) + 3) // 4) if text else 0


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    # Chat templates add a handful of tokens per message.
    return sum(estimate_text(message_text(m)) + 4 for m in messages) + 2
