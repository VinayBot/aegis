# Changelog

## 1.1.0 (2026-11-05): production pass

Security
- Production mode by default; admin role for the control plane; chaos refused in production.
- Hashed keys, constant-time lookup, public key ids; no raw key in the ledger, Redis, events, logs or APIs; masked config.
- Tenant-scoped reads (no IDOR); streamed body limit and token/message bounds.

Serving and reliability
- Real vLLM (V1 engine via vllm-metal) and the real LiteLLM proxy server as backends.
- Chain retry with full-jitter exponential backoff, only when every backend failed.
- Native tool calling and structured output pass through, are cache-keyed and never cached.
- `/v1/ws/chat`: chat over a WebSocket with mid-stream cancel.

Safety
- Llama Guard 3 on input (thresholded at P(unsafe) >= 0.9) and output (before caching).
- Streaming output redaction of emails, phones, cards and secrets.

Observability and operations
- Langfuse export (OTLP + Basic auth) beside Jaeger, verified on self-hosted Langfuse 4.67.0.
- Prometheus `/metrics`, `/healthz`, `/readyz`, JSON logs with request and trace ids.
- Evaluation gate (five golden sets), load test, production image, CI with CodeQL and Dependabot.

Fixes
- An idle dashboard WebSocket no longer holds a stopping server open.
- The docs index no longer peaks at 1.49 GB on startup (batched embedding, 444 MB).
- Five backends no longer overlap other nodes on the architecture map.

## 1.0.0 (2026-10-27): the Week 5 gateway

OpenAI-compatible SSE gateway with exact and semantic caching, coalescing, hedging, failover,
circuit breakers, rate limits, budgets, PII redaction, OpenTelemetry traces, the live website,
the Chaos Lab and the PRAHARI assistant.
