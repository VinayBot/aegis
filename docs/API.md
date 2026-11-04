# API reference

Generated from the gateway's live OpenAPI schema (`GET /openapi.json`, browsable at
http://127.0.0.1:8000/api/swagger), with notes on what the schema cannot show.

## Data plane (OpenAI-compatible)

`POST /v1/chat/completions` accepts the OpenAI chat body (`model`, `messages`, `stream`,
`temperature`, `top_p`, `max_tokens`, `stop`, `seed`, `tools`, `tool_choice`,
`parallel_tool_calls`, `response_format`...). Authenticate with `Authorization: Bearer <key>`
(keys and tenants live in `backend/config/gateway.yaml`).

Native tool calling passes through: tool definitions reach the backend, `tool_calls` deltas
stream back unchanged, non-streaming responses carry the assembled `message.tool_calls`, and the
tools are part of the cache key. A tool call is never cached (it acts on one conversation).

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
| 413 | `request_too_large` | body over `limits.max_body_bytes` (read as it streams, never whole) |
| 400 | `too_many_messages` | more than `limits.max_messages` |
| 400 | `context_length_exceeded` | prompt (with tool schemas) over `limits.max_prompt_tokens` |
| 400 | `max_tokens_too_large` | `max_tokens` over `limits.max_output_tokens` |
| 400 | `prompt_injection` | the injection guardrail matched |
| 400 | `unsafe_content` | Llama Guard 3: P(unsafe) at or above the threshold; `categories` lists the hazards |
| 400 | `invalid_request` | `messages` missing or malformed |
| 401 | `invalid_api_key` | unknown or missing key |
| 402 | `budget_exceeded` | tenant over its hard monthly budget |
| 404 | `model_not_found` | unknown public model name |
| 429 | `rate_limited` | over the key's tokens per minute (`Retry-After`) |
| 502 | `upstream_rejected` | a backend rejected the request itself (4xx other than 404/429) |
| 503 | `no_backend_available` | all breakers open, or no slot free within 10 s (`Retry-After`) |
| 503 | `safety_unavailable` | the safety classifier is down and `safety.on_error: block` |

WebSocket `/v1/ws/chat` is the same pipeline over one full-duplex socket. Send
`{"type":"chat","id":"c1","body":{...}}`; receive `start` (headers), `chunk`s, `summary`, `done`.
Send `{"type":"cancel","id":"c1"}` mid-stream: the upstream model call is cancelled and the
server answers `cancelled`. Authenticate with the header or `?key=` (browsers cannot set headers
on a WebSocket).

WebSocket `/ws/events` (admin) sends `{"type":"hello","recent":[...]}` on connect, then every lifecycle
event (`request.start`, `stage.enter`, `stage.exit`, `cache.hit`, `cache.miss`,
`coalesce.join`, `backend.try`, `hedge.fire`, `hedge.win`, `failover`,
`backend.first_token`, `request.end`, `breaker.state`, `ratelimit.reject`, `budget.warn`,
`guardrail.redact`, `chaos.action`, `scenario.step`, `health`), and a heartbeat every 3 s.

Chaos endpoints answer 403 unless the gateway runs with `AEGIS_ENV=dev` and
`AEGIS_CHAOS_ENABLED=1`; production refuses chaos even when the flag is set.

## Access levels

In production (the default), every `/api/*` route except the public ones needs a key; admin
routes need an admin key (YAML `role: admin`, or `AEGIS_ADMIN_KEY`). In dev, a caller on the
loopback interface is the local operator (admin). See docs/ARCHITECTURE.md, Security model.

| Level | Routes |
| --- | --- |
| public | `/healthz`, `/readyz`, `/api/health`, `/api/docs*`, `GET /api/scenarios`, `/v1/models` |
| viewer (any key, scoped to its tenant) | `GET /api/requests`, `GET /api/requests/{id}`, `GET /api/tenants` |
| admin | everything else under `/api`, `/ws/events`, `/metrics` |

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
| `POST /api/playground/duplicate` | N identical concurrent requests (coalescing test), fired from the gateway; optional `model`, `key`, `temperature`, `no_cache` |
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
| `GET /healthz` | Liveness: the process and its event loop respond |
| `GET /readyz` | Readiness: config loaded and at least one backend can serve (503 otherwise) |
| `GET /metrics` | Prometheus text format: requests, latency and TTFT histograms, tokens, est. cost and savings, rejections, failovers, hedges, retries, breaker states, slots, PII redactions |
| `WS /v1/ws/chat` | Chat over a WebSocket, with mid-stream cancel |
