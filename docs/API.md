# API reference

Generated from the gateway's live OpenAPI schema (`GET /openapi.json`, browsable at
http://127.0.0.1:8000/docs), with notes on what the schema cannot show.

## Data plane (OpenAI-compatible)

`POST /v1/chat/completions` accepts the OpenAI chat body (`model`, `messages`, `stream`,
`temperature`, `top_p`, `max_tokens`, `stop`, `seed`...). Authenticate with
`Authorization: Bearer <key>` (keys and tenants live in `backend/config/gateway.yaml`).

Request headers:

| Header | Effect |
| --- | --- |
| `Cache-Control: no-cache` | skip the cache lookup (the fresh answer is still written) |
| `x-aegis-summary: 1` | append an `event: aegis` SSE frame with the request summary before `[DONE]` |
| `x-aegis-client: <name>` | label the caller in traces and the ledger |

Response headers: `x-cache` (`miss`, `hit`, `semantic`, `coalesced`, `bypass`),
`x-aegis-backend`, `x-aegis-request-id`, `x-aegis-trace-id`, `x-aegis-hedged`,
`x-aegis-failovers`, `x-aegis-similarity` (semantic hits), `x-aegis-coalesced-with`,
`x-aegis-budget-warning` (over the soft budget), `x-aegis-cost-label: est. cloud equivalent`,
and `Retry-After` on 429 and 503.

Streaming responses are standard OpenAI `chat.completion.chunk` SSE frames ending in
`data: [DONE]`. A backend that fails after the first token ends the stream with `event: error`.

Errors (JSON `{"error": {"message", "type", "code", "request_id", "trace_id"}}`):

| Status | Code | When |
| --- | --- | --- |
| 400 | `prompt_injection` | the injection guardrail matched |
| 400 | `invalid_request` | `messages` missing or malformed |
| 401 | `invalid_api_key` | unknown or missing key |
| 402 | `budget_exceeded` | tenant over its hard monthly budget |
| 404 | `model_not_found` | unknown public model name |
| 429 | `rate_limited` | over the key's tokens per minute (`Retry-After`) |
| 502 | `upstream_rejected` | a backend rejected the request itself (4xx other than 404/429) |
| 503 | `no_backend_available` | all breakers open, or no slot free within 10 s (`Retry-After`) |

WebSocket `/ws/events` sends `{"type":"hello","recent":[...]}` on connect, then every lifecycle
event (`request.start`, `stage.enter`, `stage.exit`, `cache.hit`, `cache.miss`,
`coalesce.join`, `backend.try`, `hedge.fire`, `hedge.win`, `failover`,
`backend.first_token`, `request.end`, `breaker.state`, `ratelimit.reject`, `budget.warn`,
`guardrail.redact`, `chaos.action`, `scenario.step`, `health`), and a heartbeat every 3 s.

Chaos endpoints answer 403 unless the gateway runs with `AEGIS_CHAOS_ENABLED=1` (dev only).

## Every endpoint

| Endpoint | Summary |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible chat completions (SSE when stream=true) |
| `GET /v1/models` | Public model names and their backend chains |
| `GET /api/health` | Per-component status from real probes |
| `GET /api/architecture` | Live topology: nodes, edges, status, config, latency, counters |
| `GET /api/metrics` | Time series for the dashboard |
| `GET /api/tenants` | Per-tenant usage this month with budgets |
| `GET /api/requests` | Most recent requests (live feed) |
| `GET /api/requests/{request_id}` | One request: its ledger row and lifecycle events |
| `GET /api/events/recent` | Recent lifecycle events (also used by the replay recorder) |
| `GET /api/chaos` | Chaos state and action log |
| `POST /api/chaos` | Inject latency or errors, or kill/revive a backend (dev only) |
| `POST /api/chaos/revert` | Revert every active fault |
| `POST /api/breakers/{backend}/reset` | Close a breaker by hand (dev only) |
| `GET /api/scenarios` | Guided Chaos Lab scenarios |
| `POST /api/scenarios/{name}` | Start a guided scenario; progress streams as events |
| `POST /api/playground/burst` | N concurrent requests (distinct prompts by default) |
| `POST /api/playground/duplicate` | N identical concurrent requests (coalescing test) |
| `POST /api/playground/flood` | Rapid requests on the low-limit key (429 test) |
| `POST /api/selfcheck` | End-to-end probe: embed, cache lookup, 1-token generation |
| `GET /api/config` | The effective gateway configuration |
| `POST /api/cache/tune` | Adjust the semantic threshold at runtime |
| `GET /api/cache/stats` | Cache sizes |
| `POST /api/cache/flush` | Empty both caches (dev) |
| `POST /api/budgets/{tenant}/reset` | Reset a tenant's monthly spend (dev) |
| `GET /api/traces` | Recent traces from Jaeger's query API |
| `GET /api/traces/{trace_id}` | One trace as a waterfall |
| `GET /api/docs` | Project documents |
| `GET /api/docs/{slug}` | One project document (markdown) |
| `POST /api/assistant/chat` | Ask AEGIS about the project (SSE); routed through the gateway |
