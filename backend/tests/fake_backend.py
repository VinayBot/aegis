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
    fail_times: int = 0  # answer 503 to this many calls, then recover (transient blip)
    # when the request carries `tools`, answer with this tool call, streamed in OpenAI's format
    tool_call: dict[str, Any] | None = None
    calls: int = 0
    cancelled: int = 0
    completed: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)


def _frame(control: FakeControl, delta: dict[str, Any], finish: str | None = None) -> str:
    chunk = {
        "id": "fake",
        "object": "chat.completion.chunk",
        "model": control.model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


async def _tool_stream(control: FakeControl) -> Any:
    assert control.tool_call is not None
    name, args = control.tool_call["name"], json.dumps(control.tool_call["arguments"])
    yield _frame(
        control,
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": ""},
                }
            ],
        },
    )
    for i in range(0, len(args), 8):  # arguments arrive in fragments, as real servers send them
        yield _frame(
            control, {"tool_calls": [{"index": 0, "function": {"arguments": args[i : i + 8]}}]}
        )
    yield _frame(control, {}, "tool_calls")
    usage = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    yield f"data: {json.dumps({'id': 'fake', 'choices': [], 'usage': usage})}\n\n"
    yield "data: [DONE]\n\n"
    control.completed += 1


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
        if control.fail_times > 0:
            control.fail_times -= 1
            return JSONResponse({"error": {"message": "transient fake failure"}}, 503)
        if control.tool_call is not None and body.get("tools"):
            return StreamingResponse(_tool_stream(control), media_type="text/event-stream")

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
