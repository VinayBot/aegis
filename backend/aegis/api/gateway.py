"""The OpenAI-compatible data plane: POST /v1/chat/completions and GET /v1/models."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from aegis.pipeline import Pipeline, Prepared

router = APIRouter()


def _api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _sse(event: str | None, data: Any) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def _stream(prepared: Prepared, want_summary: bool) -> AsyncIterator[bytes]:
    assert prepared.events is not None
    async for kind, payload in prepared.events:
        if kind == "chunk":
            yield _sse(None, payload)
        elif kind == "error":
            yield _sse("error", payload)
        elif kind == "summary" and want_summary:
            yield _sse("aegis", payload)
    yield b"data: [DONE]\n\n"


async def _collect(prepared: Prepared) -> dict[str, Any]:
    assert prepared.events is not None
    parts: list[str] = []
    finish = "stop"
    summary: dict[str, Any] = {}
    error = None
    async for kind, payload in prepared.events:
        if kind == "chunk":
            for choice in payload.get("choices") or []:
                parts.append((choice.get("delta") or {}).get("content") or "")
                finish = choice.get("finish_reason") or finish
        elif kind == "error":
            error = payload["error"]
        elif kind == "summary":
            summary = payload
    body: dict[str, Any] = {
        "id": summary.get("request_id"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": summary.get("model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(parts)},
                "finish_reason": "error" if error else finish,
            }
        ],
        "usage": {
            "prompt_tokens": summary.get("tokens_in", 0),
            "completion_tokens": summary.get("tokens_out", 0),
            "total_tokens": summary.get("tokens_in", 0) + summary.get("tokens_out", 0),
        },
        "aegis": {k: v for k, v in summary.items() if k != "stages"},
    }
    if error:
        body["error"] = error
    return body


@router.post(
    "/v1/chat/completions", summary="OpenAI-compatible chat completions (SSE when stream=true)"
)
async def chat_completions(request: Request) -> Any:
    pipeline: Pipeline = request.app.state.pipeline
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Body must be JSON.", "code": "invalid_json"}}, 400
        )
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": {"message": "Body must be an object.", "code": "invalid_request"}}, 400
        )
    no_cache = "no-cache" in request.headers.get("cache-control", "").lower()
    client = request.headers.get("x-aegis-client", "api")[:32]
    prepared = await pipeline.handle(body, _api_key(request), no_cache=no_cache, client=client)
    if prepared.body is not None:
        return JSONResponse(prepared.body, prepared.status, headers=prepared.headers)
    if body.get("stream"):
        want_summary = request.headers.get("x-aegis-summary") == "1"
        headers = {**prepared.headers, "cache-control": "no-store", "x-accel-buffering": "no"}
        return StreamingResponse(
            _stream(prepared, want_summary),
            prepared.status,
            headers=headers,
            media_type="text/event-stream",
        )
    return JSONResponse(await _collect(prepared), prepared.status, headers=prepared.headers)


@router.get("/v1/models", summary="Public model names and their backend chains")
async def models(request: Request) -> dict[str, Any]:
    cfg = request.app.state.state.config.get()
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "owned_by": "aegis", "chain": m.chain}
            for name, m in cfg.models.items()
        ],
    }
