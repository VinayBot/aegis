"""The OpenAI-compatible data plane: POST /v1/chat/completions and GET /v1/models."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from aegis import security
from aegis.pipeline import Pipeline, Prepared

router = APIRouter()


class BodyTooLarge(Exception):
    pass


async def _read_body(request: Request, limit: int) -> bytes:
    """Read the body as it streams in and stop at the limit: an oversized request is refused
    without ever being held in memory whole (the Content-Length header is checked first, but a
    client can lie or omit it)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge
    size, chunks = 0, []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _merge_tool_calls(acc: dict[int, dict[str, Any]], deltas: list[dict[str, Any]]) -> None:
    """Assemble streamed tool-call fragments (OpenAI sends id and name once, arguments in pieces)."""
    for d in deltas:
        slot = acc.setdefault(
            int(d.get("index", len(acc))),
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if d.get("id"):
            slot["id"] = d["id"]
        if d.get("type"):
            slot["type"] = d["type"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]


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
    tool_calls: dict[int, dict[str, Any]] = {}
    finish = "stop"
    summary: dict[str, Any] = {}
    error = None
    async for kind, payload in prepared.events:
        if kind == "chunk":
            for choice in payload.get("choices") or []:
                delta = choice.get("delta") or {}
                parts.append(delta.get("content") or "")
                if delta.get("tool_calls"):
                    _merge_tool_calls(tool_calls, delta["tool_calls"])
                finish = choice.get("finish_reason") or finish
        elif kind == "error":
            error = payload["error"]
        elif kind == "summary":
            summary = payload
    message: dict[str, Any] = {"role": "assistant", "content": "".join(parts)}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        message["content"] = message["content"] or None
    body: dict[str, Any] = {
        "id": summary.get("request_id"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": summary.get("model"),
        "choices": [
            {
                "index": 0,
                "message": message,
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
    limit = request.app.state.state.config.get().limits.max_body_bytes
    try:
        raw = await _read_body(request, limit)
    except BodyTooLarge:
        return JSONResponse(
            {
                "error": {
                    "message": f"Request body over {limit} bytes.",
                    "type": "rejected",
                    "code": "request_too_large",
                }
            },
            413,
        )
    try:
        body = json.loads(raw)
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
    prepared = await pipeline.handle(
        body, security.bearer(request.headers), no_cache=no_cache, client=client
    )
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


# ------------------------------------------------------------------ WebSocket chat (full duplex)


@router.websocket("/v1/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """The same pipeline as POST /v1/chat/completions, over one long-lived socket.

    SSE is one-way; this socket is two-way, which buys two things SSE cannot do: the client can
    cancel a generation mid-stream (the upstream model call is cancelled, its slot freed, and
    the request is recorded as client_closed), and many requests share one connection.

    Client -> server:
      {"type": "chat", "id": "c1", "body": {<chat completion body>}, "no_cache": false}
      {"type": "cancel", "id": "c1"}
    Server -> client:
      {"type": "start", "id", "headers"}  {"type": "chunk", "id", "data": <chunk>}
      {"type": "summary", "id", "data"}   {"type": "done", "id"}
      {"type": "cancelled", "id"}         {"type": "error", "id", "status", "error"}

    Auth: `Authorization: Bearer <key>` on the upgrade request, or `?key=` (browsers cannot set
    headers on a WebSocket). One request runs at a time per socket; open more sockets for more.
    """
    await ws.accept()
    pipeline: Pipeline = ws.app.state.pipeline
    key = security.bearer(ws.headers) or ws.query_params.get("key")
    current: tuple[str, asyncio.Task[None]] | None = None

    async def send(message: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):  # the client may already be gone
            await ws.send_text(json.dumps(message, separators=(",", ":")))

    async def run(rid: str, body: dict[str, Any], no_cache: bool) -> None:
        prepared = await pipeline.handle(body, key, no_cache=no_cache, client="websocket")
        if prepared.body is not None:
            await send({"type": "error", "id": rid, "status": prepared.status, **prepared.body})
            return
        await send({"type": "start", "id": rid, "headers": prepared.headers})
        assert prepared.events is not None
        async for kind, payload in prepared.events:
            if kind == "chunk":
                await send({"type": "chunk", "id": rid, "data": payload})
            elif kind == "error":
                await send({"type": "error", "id": rid, "status": 200, **payload})
            elif kind == "summary":
                await send({"type": "summary", "id": rid, "data": payload})
        await send({"type": "done", "id": rid})

    limit = ws.app.state.state.config.get().limits.max_body_bytes
    try:
        while True:
            text = await ws.receive_text()
            if len(text) > limit:
                await send({"type": "error", "status": 413, "error": {"code": "request_too_large"}})
                continue
            try:
                msg = json.loads(text)
                kind, rid = msg.get("type"), str(msg.get("id") or "req")
            except Exception:
                await send({"type": "error", "status": 400, "error": {"code": "invalid_json"}})
                continue
            if kind == "chat":
                if current is not None and not current[1].done():
                    await send(
                        {"type": "error", "id": rid, "status": 409, "error": {"code": "busy"}}
                    )
                    continue
                body = msg.get("body")
                if not isinstance(body, dict):
                    await send(
                        {
                            "type": "error",
                            "id": rid,
                            "status": 400,
                            "error": {"code": "invalid_request"},
                        }
                    )
                    continue
                task = asyncio.ensure_future(run(rid, body, bool(msg.get("no_cache"))))
                current = (rid, task)
            elif kind == "cancel" and current is not None and not current[1].done():
                current[1].cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await current[1]
                await send({"type": "cancelled", "id": current[0]})
    except WebSocketDisconnect:
        pass
    finally:
        if current is not None and not current[1].done():
            current[1].cancel()  # the client left: stop the model too
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await current[1]
