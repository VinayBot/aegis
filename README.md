# AEGIS: AI Gateway & Observability

[![ci](https://github.com/VinayBot/aegis/actions/workflows/ci.yml/badge.svg)](https://github.com/VinayBot/aegis/actions/workflows/ci.yml)
[![codeql](https://github.com/VinayBot/aegis/actions/workflows/codeql.yml/badge.svg)](https://github.com/VinayBot/aegis/actions/workflows/codeql.yml)

AEGIS is one local gateway in front of every AI app. Clients call one OpenAI-compatible endpoint
(SSE or WebSocket). AEGIS authenticates them, scrubs personal data, checks content safety, enforces
token rate limits and budgets, answers from an exact or semantic cache when it can, merges
identical in-flight requests, picks a healthy model, races a slow one, fails over before the
first token, streams the answer back while redacting what should not leave, and records a trace,
a metric and a ledger row for every request.

It runs entirely on one Mac (Apple Silicon, 16 GB): an MLX primary, an Ollama fallback, real vLLM
(through the vllm-metal plugin), the real LiteLLM proxy, Llama Guard 3, Redis Stack, Jaeger (and
optionally Langfuse), the FastAPI gateway, and a live website drawn from the gateway's real API
and events.

## Week 5 coverage

| Day | Topic | Where it lives | Proof |
| --- | --- | --- | --- |
| 21 | Fallback chains | `models:` chains in [gateway.yaml](backend/config/gateway.yaml); [router.py](backend/aegis/router.py) | `test_failover_on_upstream_5xx`; Chaos Lab "Primary crashes" |
| 21 | Rate limiting | Lua token bucket per key, refunds, 429 + Retry-After ([ratelimit.py](backend/aegis/ratelimit.py)) | `test_rate_limit_*`; "Rate-limit flood" |
| 21 | Cost tracking | Per-request est. cloud cost and savings, tenant budgets ([ledger.py](backend/aegis/ledger.py), [budget.py](backend/aegis/budget.py)), Langfuse cost per generation | `test_hard_budget_blocks`; Control Room |
| 21 | Streaming | SSE relay from an async generator, OpenAI chunk format | `test_streams_openai_sse_*`; `scripts/demo_stream.sh` |
| 22 | vLLM local deployment | Real `vllm serve` (V1 engine, paged attention, continuous batching) via vllm-metal, model `aegis-vllm` | `make setup-serving`; e2e "every backend is on the map" |
| 22 | Langfuse tracing | OTLP + Basic auth to Langfuse beside Jaeger, with Langfuse trace/user/generation attributes ([telemetry.py](backend/aegis/telemetry.py)); self-hosted stack in [infra/langfuse](infra/langfuse/docker-compose.yml) | Verified on Langfuse 4.67.0: [traces](docs/evidence/langfuse-traces.png), [one trace](docs/evidence/langfuse-trace.png) |
| 22 | OpenTelemetry | One trace per request, one span per stage, GenAI semantic conventions; Prometheus `/metrics` ([prom.py](backend/aegis/prom.py)) | Traces tab; `test_prometheus_metrics` |
| 23 | Semantic cache (Redis) | Redis Stack HNSW, bge-small, threshold 0.95, partitioned by tenant and context ([cache.py](backend/aegis/cache.py)) | Eval: precision 1.0, recall 1.0 on 30 labelled pairs |
| 23 | LiteLLM proxy | The real `litellm --config` server ([infra/litellm](infra/litellm/config.yaml)) as backend `litellm-proxy`, plus the SDK adapter | `aegis-litellm-proxy` model; e2e LiteLLM test |
| 23 | Circuit breakers | Closed/open/half-open, one live probe, self-healing when idle ([breaker.py](backend/aegis/breaker.py), [health.py](backend/aegis/health.py)) | `test_three_errors_open_the_breaker…`; SIGSTOP test |
| 24 | Server-Sent Events | `POST /v1/chat/completions` with `stream: true` | every Playground request |
| 24 | Async generators | The pipeline is a chain of async generators; cancellation flows upstream | `test_websocket_cancel_stops_the_model` |
| 24 | WebSockets | `/v1/ws/chat` (full duplex, mid-stream cancel) and `/ws/events` (live events) | Playground "Transport: WebSocket" and Stop |
| 25 | Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), the live map | Architecture tab |
| 25 | Peer auditing | [docs/PEER_AUDIT.md](docs/PEER_AUDIT.md): a 30-minute audit guide and 24 findings already fixed, 10 of them from an independent review | |
| 25 | Production readiness | [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md): the capstone checklist with evidence, SLOs, runbook, known gaps | CI, `make image` |
| 25 | Final demonstration | [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | |

## Run it

```bash
make setup   # once: Python + Node deps, Playwright, mlx-lm, llama3.2:3b, llama-guard3:1b, vLLM + LiteLLM proxy
make dev     # Redis + Jaeger, MLX, Ollama, LiteLLM proxy, vLLM, gateway :8000 (dev mode), site :5173
```

| What | Where |
| --- | --- |
| Website | http://127.0.0.1:5173 |
| Gateway API (OpenAPI) | http://127.0.0.1:8000/api/swagger |
| Traces (Jaeger) | http://127.0.0.1:16686 |
| Prometheus metrics | http://127.0.0.1:8000/metrics |
| Langfuse (optional, `make langfuse-up`) | http://localhost:3100 |

Send a request like any OpenAI client:

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H 'authorization: Bearer aegis-demo-key' -H 'content-type: application/json' \
  -d '{"model":"aegis-chat","stream":true,"temperature":0,"messages":[{"role":"user","content":"What is an AI gateway?"}]}'
```

Other targets:

| Target | What it does |
| --- | --- |
| `make test` | backend (148) and frontend tests; needs `make infra` for Redis |
| `make evals` / `make evals-live` | the evaluation gate (offline / with Llama Guard) |
| `make e2e` | 21 browser tests against `make dev` |
| `make loadtest` | throughput and latency percentiles against `make dev` |
| `make image` | the production image and its smoke test |
| `make lint`, `make typecheck`, `make sweep`, `make perf` | quality checks |

The gateway is **secure by default**: without `AEGIS_ENV=dev` it runs in production mode, where
the control plane needs an admin key and chaos is refused. `make dev` sets dev mode.

## The request path

```
client -> body limit -> auth -> limits -> guardrails (PII, injection) -> rate limit -> budget
       -> exact cache -> semantic cache (embedder) -> coalescer -> safety (Llama Guard)
       -> router (slots, breakers, chain retry) -> hedger -> MLX | Ollama | vLLM | LiteLLM proxy
       -> relay (SSE / WebSocket, output redaction) -> ledger queue -> SQLite
       (every stage is an OpenTelemetry span, an event on the live stream, and a Prometheus series)
```

- **Auth:** API key to tenant; keys may be stored hashed; only a public key id leaves the auth
  check. **Limits:** body size, message count, prompt and output tokens.
- **Guardrails:** Presidio + spaCy PII redaction and a prompt-injection check (from Veritas).
  Redaction happens before the cache, the embedder or any model sees the prompt.
- **Rate limit:** tokens per minute per key, a token bucket in Redis updated atomically in Lua.
  **Budget:** monthly est. cloud-equivalent spend per tenant; soft limit warns, hard limit `402`.
- **Exact cache:** hash of tenant, model, messages and *effective* parameters (tools included).
  **Semantic cache:** the last user message under 200 tokens, HNSW cosine >= 0.95, 24 h TTL.
- **Coalescing:** identical requests in flight share one upstream call.
- **Safety:** Llama Guard 3 on cache misses (leader only), blocking at P(unsafe) >= 0.9; the
  answer is checked again before it may be cached.
- **Router:** each backend in chain order whose breaker is closed, waiting up to 2 s for a slot;
  when every backend failed, one chain retry after a full-jitter backoff. **Hedging:** no first
  token by the backend's p95 TTFT: race the next free backend; the loser is cancelled.
- **Failover (before the first token):** connect error, 5xx, 429, unknown model, or no token in
  15 s + 1 s per 1k prompt tokens. **Circuit breaker:** one timeout or three errors open it for
  30 s, then one live probe.
- **Relay:** an async generator; output redaction holds back one word so values split across
  chunks are caught. A backend dying mid-stream ends the response with an SSE `error` event.

Costs are always an **est. cloud equivalent**: local inference has no bill, so each request is
priced at the reference price in the YAML (gpt-4o-mini list price).

## The website

| Page | What it shows |
| --- | --- |
| Home | The robot, a scroll dive through its face screen into a live neuron field, live counters, Meet AEGIS |
| Architecture | The real topology, animated only by `/ws/events`: a 3D galaxy and a detailed live node map |
| Control Room | Gauges, KPIs, charts, tenants and budgets, the live request feed |
| Chaos Lab | Latency, error and kill switches per backend, a live path graph, six guided scenarios |
| Playground | Hand-made requests over SSE or WebSocket, Stop, a live per-stage trace, "send 10× at once", "rephrase and resend" |
| Traces | Jaeger traces as in-app waterfalls |
| Docs | These documents |

When the gateway is offline the site plays a recording of real traffic under a visible
"REPLAY: backend offline" banner. The **AEGIS assistant** (the robot bottom right, or `/`) answers
from these docs and live state, through the gateway itself.

## Documents

- [LEARNING.md](LEARNING.md): a guided, beginner-friendly tour (start here)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data flow, failure behaviour, security model
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md): the capstone checklist with evidence
- [docs/PEER_AUDIT.md](docs/PEER_AUDIT.md): how to audit this project, and the audit record
- [docs/DECISIONS.md](docs/DECISIONS.md): why each choice was made, and what was rejected
- [docs/API.md](docs/API.md): every endpoint, error code and access level
- [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md): every acceptance criterion, pass or fail
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md): the Day 25 demo
- [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md), [CONTRIBUTING.md](CONTRIBUTING.md)
- [DESIGN.md](DESIGN.md), [docs/SKILLS_REVIEW.md](docs/SKILLS_REVIEW.md), [docs/PLAN.md](docs/PLAN.md)
