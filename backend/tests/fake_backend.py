"""A controllable fake OpenAI-compatible server for tests.

Behaviour is set per test through `FakeControl`: first-token delay, HTTP status, mid-stream
failure, token count. It counts every completion call, so tests can assert exactly how many
upstream calls the gateway made. Also runnable as a subprocess (`python -m tests.fake_backend
--port N --model M`) so the integration test can SIGSTOP a real process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


@dataclass
class FakeControl:
    model: str
    first_token_delay: float = 0.0
    token_delay: float = 0.0
    status: int = 200
    tokens: int = 5
    fail_after: int | None = None  # drop the stream after this many tokens
    text: str = "pong"
    calls: int = 0
    cancelled: int = 0
    completed: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)


def build_app(control: FakeControl) -> Starlette:
    async def models(_: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": [{"id": control.model, "object": "model"}]})

    async def chat(request: Request) -> Any:
        body = await request.json()
        control.calls += 1
        control.requests.append(body)
        if body.get("model") != control.model:
            return JSONResponse({"error": {"message": f"model {body.get('model')} not found"}}, 404)
        if control.status != 200:
            return JSONResponse({"error": {"message": "fake failure"}}, control.status)

        async def stream() -> Any:
            try:
                await asyncio.sleep(control.first_token_delay)
                for i in range(control.tokens):
                    if control.fail_after is not None and i >= control.fail_after:
                        raise ConnectionError("fake backend died mid-stream")
                    chunk = {
                        "id": "fake",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": control.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": f"{control.text}{i} "},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    if control.token_delay:
                        await asyncio.sleep(control.token_delay)
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": "fake",
                            "object": "chat.completion.chunk",
                            "model": control.model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                    )
                    + "\n\n"
                )
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": "fake",
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 7,
                                "completion_tokens": control.tokens,
                                "total_tokens": 7 + control.tokens,
                            },
                        }
                    )
                    + "\n\n"
                )
                yield "data: [DONE]\n\n"
                control.completed += 1
            except asyncio.CancelledError:
                control.cancelled += 1
                raise

        return StreamingResponse(stream(), media_type="text/event-stream")

    return Starlette(
        routes=[Route("/v1/models", models), Route("/v1/chat/completions", chat, methods=["POST"])]
    )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model", default="fake-model")
    args = parser.parse_args()
    uvicorn.run(
        build_app(FakeControl(model=args.model, tokens=8, token_delay=0.01)),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
