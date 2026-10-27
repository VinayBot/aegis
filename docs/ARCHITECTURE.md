# Architecture

## Components

| Component | Runs as | Role |
| --- | --- | --- |
| Gateway | `uvicorn aegis.main:app` (FastAPI, async) on :8000 | the request path, control plane, WebSocket events, assistant |
| MLX primary | `mlx_lm.server` on :8081, Qwen2.5-3B-Instruct 4-bit | first choice for `aegis-chat`; 1 slot |
| Ollama fallback | `ollama serve` on :11434, llama3.2:3b | second choice; 2 slots |
| LiteLLM adapter | optional backend (`uv sync --extra litellm`) | shows LiteLLM without letting it own failover |
| Redis Stack | Docker (Colima) on :6379 | exact cache, HNSW vector index, rate-limit buckets, budgets |
| Jaeger | Docker on :16686 (UI/query), :4318 (OTLP HTTP) | traces |
| SQLite | `var/ledger.sqlite3` | usage ledger and system events |
| Embedder | in the gateway process, CPU | bge-small-en-v1.5 (384-d) via fastembed |
| Website | Vite dev server :5173 (or built and served by the gateway) | the live deck |

Every backend is an entry in `backend/config/gateway.yaml`; the gateway speaks to any
OpenAI-compatible `/v1` server over httpx, so a vLLM box is one more entry with no code change.

## One request, end to end

```
POST /v1/chat/completions  (Bearer key, stream: true)
  auth            key -> tenant, tpm, budget                        401 if unknown
  guardrails      injection regex -> 400; Presidio redaction of user messages
  ratelimit       Lua token bucket: reserve prompt_est + max_tokens -> 429 + Retry-After
  budget          tenant spend this month (est. cloud equivalent) -> 402 at hard limit
  exact_cache     sha256(tenant, model, messages, effective params) -> replay as SSE
  semantic_cache  embed last user msg (<200 tokens) -> KNN 1 in its partition -> cosine >= 0.95
  coalescer       same exact key already in flight -> follow the leader's stream
  router          chain order; skip open breakers; take a slot within 2 s or move on; 10 s -> 503
  hedger          no first token by p95 TTFT -> same request to the next free backend; first wins
  backend         httpx stream, include_usage forced
  relay           async generator -> SSE frames; summary event if x-aegis-summary: 1
  ledger_queue    ledger row, cache write, budget charge and rate-limit settlement: background
```

Each stage is a child span of the request's root span; the model call is a `chat {model}` span with
the OpenTelemetry GenAI attributes (`gen_ai.operation.name`, `gen_ai.provider.name`,
`gen_ai.request.*`, `gen_ai.response.model`, `gen_ai.usage.input_tokens/output_tokens`). Every stage
also publishes `stage.enter` / `stage.exit` events on the in-process event bus, which feeds the
WebSocket, the metrics aggregator and the replay recorder.

## Reliability behaviour

| Situation | What happens | Where |
| --- | --- | --- |
| Primary busy (its one slot taken) | wait up to 2 s, then the fallback | `router._acquire` |
| Nothing free for 10 s | `503`, `Retry-After: 2` | `router._acquire` |
| Primary slow to first token | after its p95 TTFT (min 250 ms), hedge to a free backend; loser cancelled, slot released | `router.route` |
| Connect error, 5xx, 429, unknown model | failover to the next backend before the first token; breaker counts an error | `router.route` |
| No first token in 15 s + 1 s/1k prompt tokens | timeout: breaker opens at once; failover | `router.route` |
| Three consecutive errors | breaker opens for 30 s | `breaker.py` |
| Breaker cool-down over | half-open: exactly one live request probes; success closes, failure re-opens | `breaker.py` |
| Backend process frozen (SIGSTOP) | health probes time out (0.4 s); while it is not streaming, each failed probe is an error, so three probes (about 1.5 s) open the breaker | `health.py` |
| Backend dies mid-stream | SSE `error` event, tokens estimated (±30%), no cache write | `pipeline._lead` |
| Redis down | caches skipped, rate limits fall back to an in-process bucket, health "degraded" | `ratelimit.py`, `health.py` |
| Embedder over 300 ms | semantic lookup skipped for that request | `pipeline._run` |
| All breakers open | `503` with the shortest remaining cool-down as `Retry-After` | `router._acquire` |

## Measured on this Mac

| Measurement | Value |
| --- | --- |
| Exact-cache replay | 7 ms end to end (vs 1,274 ms for the same answer from MLX) |
| Semantic hit ("How long does shipping take to Canada" vs "...Canada?") | cosine 0.986, 13 ms |
| Paraphrase just under threshold ("What is the shipping time to Canada?") | cosine 0.949: miss at 0.95 |
| 10 identical concurrent requests | 1 upstream call, 9 coalesced |
| Hedge under 4 s injected latency | fired at MLX p95 TTFT 285 ms; Ollama first token 101 ms later |
| SIGSTOP of the MLX primary | 3/3 requests answered (hedged to Ollama); breaker open after 3 probes; healed by a live probe after 30 s |
| Rate-limit flood (8 concurrent on a 1,500 tpm key) | 4 allowed, 4 × 429 with Retry-After 3 s |
| Resident memory | see docs/VALIDATION_REPORT.md |

## The control plane

`/api/health`, `/api/architecture`, `/api/metrics`, `/ws/events`, `/api/chaos`, `/api/scenarios`,
`/api/playground/*`, `/api/selfcheck`, `/api/traces`, `/api/docs`, `/api/assistant/chat`; see
[API.md](API.md). The website draws only from these. The architecture graph is generated from
`/api/architecture`, which builds its backend nodes from the YAML, so a new backend appears on the
map without a frontend change.

## Source map

```
backend/aegis/
  main.py          app factory, lifespan (build, warm, start background tasks, clean shutdown)
  pipeline.py      the request path, stage spans/events, bookkeeping
  router.py        slots, breakers, hedging, failover, relay
  breaker.py       circuit breaker state machine
  cache.py         exact + semantic cache (Redis Stack, HNSW)
  embedder.py      bge-small via fastembed, in-process
  coalesce.py      singleflight
  ratelimit.py     TPM token bucket (Lua) with in-process fallback
  budget.py        tenant budgets
  guardrails/      PII redaction + injection check (adapted from Veritas)
  backends/        OpenAI-compatible httpx client, LiteLLM adapter, protocol
  chaos.py         fault injection (latency, errors, real SIGSTOP)
  health.py        probes; feeds breakers
  metrics.py       per-second buckets from the event bus
  ledger.py        batched SQLite writer
  architecture.py  live topology
  assistant.py     docs index + grounded answers through the gateway
  scenarios.py     guided chaos scenarios, playground load helpers
  api/             HTTP: gateway (OpenAI) and control plane
frontend/src/
  lib/live.ts      the live/replay data layer (WebSocket, polling, fixture playback)
  pages/           Home, Architecture, ControlRoom, ChaosLab, Playground, Traces, Docs
  components/arch/ graph layout, nodes, packet engine
  components/fx/   sky shader, reveals, ticker, spotlight
  scenes/          three.js star field and neural interior (bloom)
```
