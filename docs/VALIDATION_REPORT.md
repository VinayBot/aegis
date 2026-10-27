# Validation report

Measured on 2026-10-27 on the build machine: an Apple Silicon Mac with 16 GB, a 120 Hz display,
and Colima (2 CPU / 2 GB) for Redis Stack and Jaeger. The system was the real one (`make dev`):
MLX Qwen2.5-3B-4bit as primary, Ollama llama3.2:3b as fallback. Nothing below is simulated.

**Verdict: every functional criterion passes. Two performance targets are partial:**
- Mobile Lighthouse scores 61–69 (the 85 target applies to desktop Home, which scores 91).
- The Architecture page holds 74–82 fps under traffic, not 120.

## Test suites

| Suite | Command | Result |
| --- | --- | --- |
| Backend unit + HTTP integration + SIGSTOP | `make test-backend` | **61 passed** (43 s) |
| Lint and format (backend) | `uv run ruff check .`, `ruff format --check .` | **pass** |
| Types (backend) | `uv run mypy aegis` | **pass** (no issues in 34 files) |
| Types (frontend) | `npx tsc -b` | **pass** |
| Lint (frontend, bans `Math.random`) | `npx eslint src` | **pass** |
| Frontend unit | `npm test` (vitest) | **7 passed**, including the `Math.random` guard |
| End-to-end, real system, dev server | `make e2e` (Playwright) | **14 passed**, twice in a row |
| End-to-end, production build served by the gateway | `AEGIS_SITE_URL=http://127.0.0.1:8000 npm run e2e` | **14 passed** |
| Page sweep, desktop and phone width, dev and production | `make sweep` | **20 of 20 clean** |

The e2e suite covers:
- the Home scroll journey through all five regions;
- the architecture graph built from `/api/architecture`;
- the 3D galaxy, with comets following real requests;
- killing the primary turns its breaker red within 2 s;
- an assistant answer with a citation and a trace chip;
- the offline switch to labelled replay;
- the Playground: a streamed answer with its trace, then an exact cache hit on resend;
- the Playground: ten identical requests make one upstream call (nine coalesced);
- the Chaos Lab kill switch: the primary freezes, traffic is answered by the fallback, revert restores it;
- Traces: picking a trace shows its waterfall and its Jaeger link;
- Architecture: clicking a node opens its details, and so does tapping one on a phone;
- Docs: links between documents open inside the site;
- a 404 page with a way back, and the Pause motion control.

The page sweep (`make sweep`) loads all 10 routes at 1440 px and 390 px. On each load it checks for:
- console errors and warnings;
- failed or 4xx/5xx requests;
- horizontal overflow;
- exactly one `h1`;
- images without `alt`;
- buttons or links without a name.

All 20 loads are clean. Two further checks also passed:
- **Offline replay:** every page with the API and WebSocket blocked shows the replay banner and has no errors.
- **Docs:** all 9 documents load, and all 15 links between them resolve.

## Gateway criteria

| Criterion | Evidence | Result |
| --- | --- | --- |
| SSE streaming, OpenAI-compatible | `test_streams_openai_sse_and_records_summary`, `test_unsummarised_stream_is_pure_openai` | pass |
| Auth by key | `test_auth_rejects_unknown_key` | pass |
| PII redacted before the model | `test_pii_never_reaches_the_backend`; live *PII in prompt* scenario | pass |
| Prompt injection blocked | `test_prompt_injection_is_blocked_before_any_model` (regex; DeBERTa dropped, see DECISIONS) | pass |
| TPM token bucket (Lua) with refund | `test_rate_limit_returns_429_with_retry_after`, `test_rate_limit_refunds_unused_reservation`; live flood: 4 × 429 | pass |
| Hard budget | `test_hard_budget_blocks` (402) | pass |
| Exact cache | test plus live: 7 ms hit | pass |
| Semantic cache (bge-small, 0.95, under 200 tokens) | tests plus live: a paraphrase at 0.986 hits, 0.949 misses | pass |
| No temperature injection (correction 1) | `test_omitted_temperature_uses_provider_default_not_zero`, `test_different_temperature_is_a_different_cache_entry` | pass |
| Coalescing | `test_coalescing_makes_exactly_one_upstream_call`; live: 10 requests → 1 upstream | pass |
| Breakers (3 errors or 1 timeout → open 30 s → one probe) | `test_three_errors_open_the_breaker…`, `test_first_token_timeout…` | pass |
| Busy is not broken | `test_busy_primary_spills_to_fallback_after_slot_wait` | pass |
| Hedging at rolling p95 TTFT | `test_hedge_fires_and_cancels_the_loser`; live with 4 s injected latency | pass |
| Failover before the first token; SSE error mid-stream | `test_failover_on_upstream_5xx`, `test_midstream_failure_sends_sse_error_and_does_not_cache` | pass |
| All breakers open → 503 + Retry-After | `test_all_breakers_open_returns_503_with_retry_after` | pass |
| Real crash (SIGSTOP of MLX) | `test_sigstop_primary_breaker_opens_and_traffic_fails_over`; live: recovered and healed via probe | pass |
| Batched ledger (correction 2) | background SQLite writer; ledger/cost endpoints in `test_control_plane_endpoints` | pass |
| Cost shown as "est. cloud equivalent" (correction 3) | Control Room and Playground labels | pass |
| OTel gen_ai spans to Jaeger | `test_every_stage_is_a_span_with_genai_conventions`; traces visible in Jaeger and the Traces page | pass |
| Chaos endpoints dev-only | 403 unless `AEGIS_CHAOS_ENABLED=1` (`aegis/chaos.py`, `api/control.py`) | pass |
| LiteLLM optional adapter | `aegis-litellm` model; shown as optional, the gateway runs without it | pass |

## Site criteria

| Criterion | Result |
| --- | --- |
| No `Math.random` in data paths | **pass**: an ESLint ban plus a unit test; decoration uses a seeded mulberry32 |
| Replay banner when the backend is offline | **pass** (e2e); the fixture is 686 real recorded events |
| All pages: Home, Architecture, Control Room, Chaos Lab, Playground, Traces, Docs | **pass** |
| Assistant: grounded, routed through the gateway, trace chip, citations | **pass** (e2e, plus 12 hand-checked questions below) |
| Reduced motion and the "Pause motion" control | **pass**: every effect has a still form; the 3D scenes switch to on-demand rendering |
| Accessibility (Lighthouse) | **100** on Home, Architecture and Control Room, desktop and mobile |

## Lighthouse (production build, `vite preview`, Lighthouse 12.8.2)

| Page | Desktop perf | Mobile perf | A11y | Best practices | Desktop LCP / TBT / CLS |
| --- | --- | --- | --- | --- | --- |
| Home | **91** | 69 | 100 | 100 | 1.0 s / 210 ms / 0.001 |
| Architecture | 88 | 67 | 100 | 100 | 1.1 s / 240 ms / 0.001 |
| Control Room | 95 | 61 | 100 | 100 | 1.3 s / 0 ms / 0.001 |

**Home ≥ 85: pass on desktop.** On mobile (simulated slow 4G and 4× CPU throttling) all pages are
below 85, because LCP is 5–6 s from the JS bundle on a throttled CPU. This is recorded as a known
gap. Earlier builds scored 96–98 on desktop; the later 3D assistant and the galaxy cost a few points.

## Frame times (Chromium on the Mac GPU, `make perf`)

| Scenario | fps | p95 frame | janky frames |
| --- | --- | --- | --- |
| Home: hero idle | 120 | 9.0 ms | 0 / 479 |
| Home: scroll through the neuron dive | 118 | 9.1 ms | 1 / 628 |
| Home: Meet AEGIS + docked robot | 120 | 8.6 ms | 0 / 478 |
| Architecture (detailed): burst traffic | 82 | 25.0 ms | 13 / 325 |
| Architecture: 3D galaxy + traffic | 74 | 17.6 ms | 9 / 295 |
| Control Room: idle | 118 | 8.9 ms | 1 / 472 |

All scenarios exceed 60 fps. The two Architecture views drop below the display's 120 Hz under burst
traffic (bloom post-processing plus dozens of animated packets).

## Memory (`make memory`)

| Process | RSS |
| --- | --- |
| gateway (FastAPI + embedder + spaCy) | 65 MiB |
| mlx_lm.server (Qwen2.5-3B 4-bit) | 38 MiB* |
| ollama (llama3.2:3b loaded) | 2164 MiB |
| vite dev server | 83 MiB |
| Redis Stack / Jaeger containers | 8 / 110 MiB |
| **total** | **2.41 GiB** |

\* RSS undercounts MLX: its weights (about 1.8 GB) live in Metal unified memory, which RSS does not
show. The Colima VM also reserves 2 GiB. The realistic footprint is about 6 GB, which is well inside
16 GB with one answer model per backend.

## Every chaos scenario, run alone against the real models

| Scenario | Narrated result |
| --- | --- |
| Primary crashes | 3/3 answered while MLX was frozen; breaker open; next request straight to Ollama; probe closed the breaker |
| Traffic burst | 12 requests: 6 on MLX, 6 on Ollama, all 200 |
| Slow primary | p95 TTFT 461 ms, 4 s latency injected: hedged, first token from Ollama |
| Duplicate storm | 10 answers, 1 upstream call, 9 coalesced |
| Rate-limit flood | 4 allowed, 4 × 429 with Retry-After 3 s |
| PII in prompt | email, phone and name redacted before any model; counts only in the trace |

## Assistant accuracy (12 questions, checked against `config/gateway.yaml` and the docs)

These questions were answered correctly after the fixes below:
- how long a breaker stays open (30 s);
- how many errors open it (3);
- when hedging fires;
- the semantic threshold (0.95);
- the slot wait (2 s);
- what happens when Redis is down;
- what happens when a backend dies mid-stream;
- the first-token timeout;
- why busy is not broken;
- whether the primary is healthy right now;
- which backend is the fallback;
- the live cache hit rate (verified against `/api/metrics`).

An off-topic question ("the capital of France") got the fixed refusal.

Caveat: this is a 3B model, so an unusual phrasing can still produce a wrong number. Its context now
states the key settings as plain sentences, which removed every error seen in this set.

## Bugs found by the validation itself

- redis-py 8 returns FT.SEARCH replies as dicts, so every semantic lookup missed. Fixed with
  `_first_result`.
- A test-only span exporter was shut down between tests. Fixed with a per-fixture exporter and a
  simple span processor.
- The architecture graph waited for its next poll after a kill (1.9 s). Fixed: health and breaker
  events now trigger an immediate refresh.
- Galaxy labels: drei `Html` lost the first label's React root. Replaced with one DOM layer pinned
  every frame.

Found by the deep-testing pass:
- **Duplicate storm said "10 upstream, 0 coalesced".** Each storm's prompt was nearly identical to
  the previous one, so the semantic cache answered it, and the narration miscounted those cache
  hits as upstream calls. The built-in storm now bypasses the cache (the coalescer runs after it)
  and the narration counts each outcome separately. A regression test runs the storm twice.
- **The Playground's "Send this 10× at once" coalesced only 8.** A browser opens at most six
  streams per host, so the last four queued behind the first answer. The ten requests are now
  fired from the gateway with the Playground's own settings.
- **Clicking or tapping a node on the detailed map often did nothing.** The hover highlight
  re-rendered the node between press and release, so the click went to the background. The press
  target is now honoured. Every phone tap hit this; two e2e tests cover it.
- **`/docs` on the production build showed FastAPI's Swagger page.** The site and Swagger shared
  the path; Swagger moved to `/api/swagger`.
- **The PII guardrail redacted "Redis", "Ollama" and "Qwen" as people.** Questions about the
  stack reached the model as `<PERSON>`. Stack names are now excluded, but a real name next to
  one is still redacted (unit tests for both).
- **The assistant said a breaker "stays open for 3 consecutive errors".** The 3B model misread a
  docs table. Key settings and failure behaviours are now given to it as plain sentences built
  from the live config, and the live state names the primary and the fallback.
- The Docs page had two `h1` headings; links between documents led to a 404; an unknown URL showed
  React Router's raw error; the React Flow attribution was hidden against its licence; the reel
  mixed `animation` with `animationPlayState`. All fixed.
- On phones, the docked robot covered each page's last controls. It is now smaller there, without
  the bubble, and pages have room to scroll clear of it.

## Not done or changed from the spec

- DeBERTa injection classifier: dropped for the 16 GB budget; a regex check stands in, as documented.
- The Architecture Overview and Trace tabs were removed at the user's request; their content now
  lives in the Detailed view.
- The mobile Lighthouse target (see above).
