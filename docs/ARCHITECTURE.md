# Architecture

## Components

| Component | Runs as | Role |
| --- | --- | --- |
| Gateway | `uvicorn aegis.main:app` (FastAPI, async) on :8000 | the request path, control plane, WebSocket events, assistant |
| MLX primary | `mlx_lm.server` on :8081, Qwen2.5-3B-Instruct 4-bit | first choice for `aegis-chat`; 4 slots (it batches; throughput stops growing past 4 here) |
| Ollama fallback | `ollama serve` on :11434, llama3.2:3b | second choice; 2 slots |
| LiteLLM adapter | the LiteLLM Python SDK as one backend | provider translation inside the gateway process |
| LiteLLM proxy | the real `litellm --config` server on :4000 (`infra/litellm/config.yaml`) | an OpenAI-compatible proxy in front of Ollama; its retries off so AEGIS owns failover |
| vLLM | `vllm serve` via the vllm-metal plugin on :8090, Qwen2.5-0.5B 4-bit | the real V1 engine (paged attention, continuous batching) on Apple Silicon; 8 slots |
| Llama Guard 3 1B | on Ollama | content safety: input on cache misses, output before caching |
| Redis Stack | Docker (Colima) on :6379 | exact cache, HNSW vector index, rate-limit buckets, budgets |
| Jaeger | Docker on :16686 (UI/query), :4318 (OTLP HTTP) | traces |
| Langfuse (optional) | Docker, v4.67.0, `make langfuse-up`, UI :3100 | the same OTLP spans as Langfuse traces, generations, users and costs |
| Prometheus endpoint | `GET /metrics` on the gateway | counters, histograms and gauges for any Prometheus/Grafana |
| SQLite | `var/ledger.sqlite3` | usage ledger and system events |
| Embedder | in the gateway process, CPU | bge-small-en-v1.5 (384-d) via fastembed |
| Website | Vite dev server :5173 (or built and served by the gateway) | the live deck |

Every backend is an entry in `backend/config/gateway.yaml`; the gateway speaks to any
OpenAI-compatible `/v1` server over httpx, so a vLLM GPU box is one more entry with no code change.
The YAML reads `${VAR:-default}`, so the same file serves the laptop and the container image.

## One request, end to end

```
POST /v1/chat/completions  (Bearer key, stream: true)       (or the same over /v1/ws/chat)
  body limit      streamed read, stops at 1 MB                       413
  auth            constant-time key lookup (plain or sha256:) -> tenant, public key id   401
  limits          messages, roles, prompt tokens, max_tokens         400 with a specific code
  guardrails      injection regex -> 400; Presidio redaction of user messages
  ratelimit       Lua token bucket: reserve prompt_est + max_tokens -> 429 + Retry-After
  budget          tenant spend this month (est. cloud equivalent) -> 402 at hard limit
  exact_cache     sha256(tenant, model, messages, effective params) -> replay as SSE
  semantic_cache  embed last user msg (<200 tokens) -> KNN 1 in its partition -> cosine >= 0.95
  coalescer       same exact key already in flight -> follow the leader's stream
  safety          leader only: Llama Guard 3 on the prompt; block when P(unsafe) >= 0.9 -> 400
  router          chain order; skip open breakers; take a slot within 2 s or move on; 10 s -> 503
                  every backend failed? one chain retry after full-jitter backoff
  hedger          no first token by p95 TTFT -> same request to the next free backend; first wins
  backend         httpx stream, include_usage forced
  relay           async generator -> SSE frames; output guard redacts emails, phones, cards and
                  secrets in-stream (a hold-back buffer sees values split across chunks)
  ledger_queue    ledger row, budget charge and rate-limit settlement: background; the cache
                  write waits for Llama Guard's verdict on the answer (unsafe -> never cached)
```

Each stage is a child span of the request's root span; the model call is a `chat {model}` span with
the OpenTelemetry GenAI attributes (`gen_ai.operation.name`, `gen_ai.provider.name`,
`gen_ai.request.*`, `gen_ai.response.model`, `gen_ai.usage.input_tokens/output_tokens`). Every stage
also publishes `stage.enter` / `stage.exit` events on the in-process event bus, which feeds the
WebSocket, the metrics aggregator, the Prometheus exporter and the replay recorder. Spans go to
Jaeger and, with keys set, to Langfuse (OTLP + Basic auth), carrying Langfuse's own attributes:
the trace name, the tenant as user, tags, and the model call as a `generation` with its cost.
Every log line is JSON with the request id, trace id and tenant of the request that wrote it.
A caller's W3C `traceparent` header is honoured: the gateway's root span joins the caller's trace,
so one trace runs from a client app (NEXUS, Veritas) through the gateway to the model.

## Reliability behaviour

| Situation | What happens | Where |
| --- | --- | --- |
| Primary busy (its one slot taken) | wait up to 2 s, then the fallback | `router._acquire` |
| Nothing free for 10 s | `503`, `Retry-After: 2` | `router._acquire` |
| Primary slow to first token | after its p95 TTFT (min 250 ms), hedge to a free backend; loser cancelled, slot released | `router.route` |
| Connect error, 5xx, 429, unknown model | failover to the next backend before the first token; breaker counts an error | `router.route` |
| No first token in 15 s + 1 s/1k prompt tokens | timeout: breaker opens at once; failover | `router.route` |
| Three consecutive errors | breaker opens for 30 s | `breaker.py` |
| Half of the last 30 s of requests failed (at least 10) | breaker opens for 30 s: catches a backend failing every other request, which never makes three in a row | `breaker.py` |
| Breaker cool-down over | half-open: exactly one live request probes; success closes, failure re-opens | `breaker.py` |
| Breaker half-open and no traffic | the health monitor sends that one live request itself (one token), so an idle gateway heals | `health.py` |
| Host paused (laptop sleep: wall clock jumps past the check interval) | probe failures are not counted for 10 s; they are the host's, not the backends' | `health.py` |
| Backend process frozen (SIGSTOP) | health probes time out (0.4 s); while it is not streaming, each failed probe is an error, so three probes (about 1.5 s) open the breaker | `health.py` |
| Backend dies mid-stream | SSE `error` event, tokens estimated (±30%), no cache write | `pipeline._lead` |
| Redis down | caches skipped, rate limits fall back to an in-process bucket, health "degraded" | `ratelimit.py`, `health.py` |
| Embedder over 300 ms | semantic lookup skipped for that request | `pipeline._run` |
| All breakers open | `503` with the shortest remaining cool-down as `Retry-After`; no retry (it cannot help) | `router._acquire` |
| Every backend failed (not merely open) | one chain retry after a full-jitter backoff (0-200 ms, then 0-400 ms; capped 2 s), never past the queue deadline | `pipeline._route_with_retry` |
| Llama Guard slow or down | `on_error: allow` (default): served, `safety.skipped` in the trace; `block`: 503 + Retry-After | `pipeline._safety_input` |
| Client closes the stream or sends `cancel` on the WebSocket | the generator is cancelled, the upstream call is cut and its slot freed; recorded as `client_closed` | `pipeline._lead`, `api/gateway.ws_chat` |
| Server shutting down with dashboards connected | WebSocket handlers notice the close at once; uvicorn exits within its 5 s grace | `api/control.events_ws` |

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

## Security model

- **Secure by default.** `AEGIS_ENV` defaults to `production`: the control plane needs an admin
  credential (a YAML key with `role: admin`, or `AEGIS_ADMIN_KEY`), chaos is refused even if
  switched on, plaintext keys in the YAML are ignored (only `sha256:` entries authenticate, so
  the repo's public demo keys cannot), and logs are JSON. `scripts/dev.sh` sets `dev`, where a
  loopback caller is the local operator, so the demo site needs no key, unless the browser says
  the request came from another website (Origin check: no CSRF or WebSocket hijack from a page
  open in the same browser). Loopback is never trusted in production.
- **Tokens.** A client may trade its API key for a short-lived HS256 JWT
  (`POST /v1/auth/token`, 15 min by default) carrying the key's id, tenant and role, for browsers
  and mobile apps that should not hold a long-lived key. Only `HS256` is accepted (never `none`),
  issuer and expiry are required, a token cannot mint another, and deleting the key from the YAML
  revokes its tokens at once.
- **Keys.** YAML entries may be `sha256:<hex>`; lookup hashes the presented key and compares
  against every entry with `hmac.compare_digest`. Only a key's public `id` leaves `security.py`:
  ledger rows, Redis key names, events, logs and every API response use it. `/api/config` masks
  backend credentials; backend keys can come from the environment (`api_key_env`).
- **Access levels.** Public: `/healthz`, `/readyz`, `/api/health`, docs, scenario list. Viewer
  (any key): requests and tenants, filtered by tenant in SQL, so another tenant's request id is a
  404. Admin: everything that changes state or spans tenants, including `/ws/events` and
  `/metrics`.
- **Key guessing.** 20 failed authentications from one address within a minute earn a 429 with
  `Retry-After` on every entry point (HTTP, WebSocket, token exchange, feedback, control plane).
  Behind a reverse proxy, uvicorn's `--proxy-headers` makes the address the real client's.
- **Input bounds.** Body size (streamed, 413), message count, roles, prompt tokens and
  `max_tokens` are checked before any guardrail, cache or model.
- **Content.** Presidio + regex redaction on input, Llama Guard 3 on input and output, streaming
  output redaction. Traces and logs carry counts, never prompt or answer text.
- **Container.** Non-root (uid 10001), read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, pinned base images; CodeQL, pip-audit and npm audit in CI.

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
  guardrails/      PII redaction + injection check (adapted from Veritas), Llama Guard safety,
                   streaming output redaction
  security.py      keys, principals, access levels, tenant scoping
  prom.py          Prometheus exporter fed by the event bus
  logcontext.py    JSON logs with request/trace/tenant context
  retry.py         full-jitter exponential backoff
  backends/        OpenAI-compatible httpx client, LiteLLM adapter, protocol
  chaos.py         fault injection (latency, errors, real SIGSTOP)
  health.py        probes; feeds breakers
  metrics.py       per-second buckets from the event bus
  ledger.py        batched SQLite writer
  architecture.py  live topology
  assistant.py     docs index + grounded answers through the gateway
  scenarios.py     guided chaos scenarios, playground load helpers
  api/             HTTP + WebSocket: gateway (OpenAI, /v1/ws/chat) and control plane
backend/evals/     golden datasets and the evaluation gate (make evals)
frontend/src/
  lib/live.ts      the live/replay data layer (WebSocket, polling, fixture playback)
  pages/           Home, Architecture, ControlRoom, ChaosLab, Playground, Traces, Docs
  components/arch/ graph layout, nodes, packet engine
  components/fx/   sky shader, reveals, ticker, spotlight
  scenes/          three.js star field and neural interior (bloom)
```
