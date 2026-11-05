"""Structured JSON logs, correlated with traces.

Every log line is one JSON object with the time, level, logger and message, plus the request id,
trace id and tenant of the request that produced it (carried in contextvars, so concurrent
requests never mix). A trace id in a log line opens the same request in Jaeger or Langfuse.

    {"ts":"2026-11-05T10:01:02.345Z","level":"INFO","logger":"prahari.request","msg":"request",
     "request_id":"req_…","trace_id":"4bf9…","tenant":"demo","status":"ok","latency_ms":812.4}

`PRAHARI_LOG_FORMAT=text` gives the same fields as readable lines for a terminal.
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

_FIELDS = ("request_id", "trace_id", "tenant")
_ctx: dict[str, ContextVar[str | None]] = {
    f: ContextVar(f"prahari_{f}", default=None) for f in _FIELDS
}
# attributes every LogRecord has; anything else passed via `extra=` is a structured field
# (uvicorn's `color_message` is the same text with terminal colour codes: not a field)
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}


def bind(**values: str | None) -> None:
    for key, value in values.items():
        if key in _ctx:
            _ctx[key].set(value)


def current() -> dict[str, str]:
    return {k: v for k, var in _ctx.items() if (v := var.get()) is not None}


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    out: dict[str, Any] = current()
    for key, value in vars(record).items():
        if key not in _RESERVED and not key.startswith("_"):
            out[key] = value
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        payload: dict[str, Any] = {
            "ts": f"{ts}.{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_fields(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record)} {record.levelname} {record.name} {record.getMessage()}"
        fields = " ".join(f"{k}={v}" for k, v in _fields(record).items())
        out = f"{base} {fields}" if fields else base
        if record.exc_info:
            out += "\n" + self.formatException(record.exc_info)
        return out


def setup(fmt: str = "json", level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(
        logging.WARNING
    )  # one structured line per request instead
    logging.getLogger("httpx").setLevel(logging.WARNING)  # probes run twice a second
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
