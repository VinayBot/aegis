# AEGIS: AI Gateway & Observability

AEGIS is one local gateway in front of every AI app. Clients call one OpenAI-compatible endpoint;
AEGIS authenticates them, scrubs personal data, enforces token rate limits and budgets, answers from
an exact or semantic cache when it can, merges identical in-flight requests, picks a healthy model,
races a slow one, fails over before the first token, streams the answer back as Server-Sent Events,
and records a trace and a ledger row for every request.

It runs entirely on one Mac (Apple Silicon, 16 GB): an MLX primary model, an Ollama fallback, Redis
Stack, Jaeger, the FastAPI gateway and a live website that is drawn from the gateway's real API and
events.

## Run it

```bash
make setup   # once: Python + Node deps, Playwright Chromium, mlx-lm, llama3.2:3b
make dev     # Redis + Jaeger (Colima), mlx_lm.server, Ollama, gateway :8000, site :5173
```

| What | Where |
| --- | --- |
| Website | http://127.0.0.1:5173 |
| Gateway API (OpenAPI) | http://127.0.0.1:8000/api/swagger |
| Traces (Jaeger) | http://127.0.0.1:16686 |

Send a request like any OpenAI client:

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H 'authorization: Bearer aegis-demo-key' -H 'content-type: application/json' \
  -d '{"model":"aegis-chat","stream":true,"temperature":0,"messages":[{"role":"user","content":"What is an AI gateway?"}]}'
```

Other targets: `make test` (backend + frontend tests, needs `make infra`), `make e2e` (browser tests
against `make dev`), `make lint`, `make typecheck`, `make record-fixture`, `make memory`, `make perf`, `make sweep` (every page at desktop and phone width).

## The request path

```
client -> auth -> guardrails -> rate limit -> budget -> exact cache -> semantic cache (embedder)
       -> coalescer -> router (slots, breakers) -> hedger -> MLX primary | Ollama fallback | LiteLLM
       -> SSE relay -> ledger queue -> SQLite        (every stage is its own OpenTelemetry span)
```

- **Auth:** API key to tenant (`backend/config/gateway.yaml`, `keys:`).
- **Guardrails:** Presidio + spaCy PII redaction and a prompt-injection check, adapted from the
  Veritas project (`backend/aegis/guardrails/`). Redaction happens before the cache, the embedder or
  any model sees the prompt.
- **Rate limit:** tokens per minute per key, a token bucket in Redis updated atomically in Lua. Over
  the limit: `429` with `Retry-After`. Unused reservations are refunded when a request finishes.
- **Budget:** monthly est. cloud-equivalent spend per tenant. Soft limit: a warning header. Hard
  limit: `402`.
- **Exact cache:** hash of tenant, model, messages and *effective* parameters (the provider's real
  defaults are filled in; temperature is never injected).
- **Semantic cache:** partitioned by everything except the last message; the vector is the last user
  message, only when it is under 200 tokens (long RAG prompts get exact match only). HNSW, cosine,
  bge-small 384-d embedded in-process on the CPU, threshold 0.95, 24 h TTL. `Cache-Control: no-cache`
  bypasses the lookup.
- **Coalescing:** identical requests in flight at the same moment share one upstream call.
- **Router:** each backend in YAML order whose breaker is closed; wait up to 2 s for a free slot
  (MLX 1, Ollama 2) or move on; nothing in 10 s: `503` + `Retry-After`. The first-token clock starts
  only when a slot is held: busy is not broken.
- **Hedging:** no first token by the backend's rolling p95 TTFT: the same request goes to the next
  backend with a free slot; the first token wins and the loser is cancelled.
- **Failover (before the first token):** connect error, 5xx, 429, unknown model, or no token within
  15 s + 1 s per 1k prompt tokens.
- **Circuit breaker:** one timeout or three errors open it for 30 s; then one live request probes it.
- **Relay:** an async generator relays the upstream stream (`include_usage` forced). A backend that
  dies mid-stream ends the response with an SSE `error` event; it is not cached.
- **Ledger and cache writes** go to background queues, never the request path.

Costs are always an **est. cloud equivalent**: local inference has no bill, so each request is priced
at the reference price in the YAML (gpt-4o-mini list price), and cache hits and coalesced followers
count that price as saved. Estimates are flagged ±30% when a backend omits `usage`.

## The website

| Page | What it shows |
| --- | --- |
| Home | The robot, a scroll dive through its face screen into a live neuron field, a tunnel of the site's pages, live counters, and Meet AEGIS |
| Architecture | Two views of the real topology, animated only by `/ws/events`: a 3D galaxy (one comet per request) and a detailed live node map; trace replay, self-check, export |
| Control Room | Gauges, heartbeat, KPIs, charts, tenants and the live request feed |
| Chaos Lab | Latency, error and kill switches per backend, and six guided scenarios |
| Playground | Hand-made requests with a live per-stage trace; "send 10× at once"; "rephrase and resend" |
| Traces | Jaeger traces as in-app waterfalls |
| Docs | These documents |

When the gateway is offline the site switches to **Replay**: a recording of real traffic from this
machine (`frontend/public/replay/fixture.json`) plays back with its original timing, under a visible
"REPLAY: backend offline" banner.

The **AEGIS assistant** (the 3D robot docked bottom right, or press `/`) answers from these docs and
from live state, cites its sources, and sends every answer through the gateway itself, with the trace
chip underneath.

## Documents

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data flow, failure behaviour
- [docs/DECISIONS.md](docs/DECISIONS.md): why each choice was made, and what was rejected
- [docs/API.md](docs/API.md): every endpoint
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md): the Day 25 demo, step by step
- [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md): every acceptance criterion, pass or fail
- [docs/SKILLS_REVIEW.md](docs/SKILLS_REVIEW.md): the design skills used and what was checked
- [DESIGN.md](DESIGN.md): the design system
- [docs/PLAN.md](docs/PLAN.md): the build plan and file tree
