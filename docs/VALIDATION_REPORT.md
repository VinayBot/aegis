# Validation report

Updated 2026-11-05 for the production pass (section below); first measured on 2026-10-27 on the build machine: an Apple Silicon Mac with 16 GB, a 120 Hz display,
and Colima (2 CPU / 2 GB) for Redis Stack and Jaeger. The system was the real one (`make dev`):
MLX Qwen2.5-3B-4bit as primary, Ollama llama3.2:3b as fallback. Nothing below is simulated.

**Verdict: every acceptance criterion passes.** Home scores 100 in Lighthouse on desktop and 91 on
mobile (target 85), and every scenario runs at 60 fps or more. Known limits:
- Mobile Lighthouse on Architecture and Control Room is 81–82, below 85; the target applies to Home only.
- The Architecture page runs at 60–80 fps while the local models answer, not the display's 120.
- The assistant runs on a 3B model, so an unusual phrasing can still get a wrong number.

## Test suites

| Suite | Command | Result |
| --- | --- | --- |
| Backend unit + HTTP integration + SIGSTOP + eval gate | `make test-backend` | **148 passed**, 90% line coverage |
| Evaluation gate (five golden sets, Llama Guard included) | `make evals-live` | **5 of 5 PASS** |
| Lint and format (backend) | `uv run ruff check .`, `ruff format --check .` | **pass** |
| Types (backend) | `uv run mypy` | **pass** (no issues in 43 files) |
| Types (frontend) | `npx tsc -b` | **pass** |
| Lint (frontend, bans `Math.random`) | `npx eslint src` | **pass** |
| Frontend unit | `npm test` (vitest) | **8 passed**, including the `Math.random` guard |
| End-to-end, real system, dev server | `make e2e` (Playwright) | **21 passed** |
| End-to-end, production build served by the gateway | `AEGIS_SITE_URL=http://127.0.0.1:8000 npm run e2e` | 16 passed (2026-10-27) |
| Production image | `make image` | **pass**: alive, 503 not ready without models, 401 anonymous admin, uid 10001, healthy |
| Page sweep at 1440, 1280, 768 and 390 px | `make sweep` | **40 of 40 clean** |
| Tab switching, dev and production | `node frontend/tools/tabs.mjs` | every tab fully shown in 0.1–0.3 s, never blank |

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
- a 404 page with a way back, and the Pause motion control;
- LiteLLM: the optional adapter is up and serves a real request through the gateway;
- the Chaos Lab's live path graph shows a killed primary's open breaker.

The page sweep (`make sweep`) loads all 10 routes at 1440, 1280, 768 and 390 px. On each load it checks for:
- console errors and warnings;
- failed or 4xx/5xx requests;
- horizontal overflow;
- exactly one `h1`;
- images without `alt`;
- buttons or links without a name.

All 40 loads are clean. Two further checks also passed:
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
| LiteLLM optional adapter | `litellm==1.80.0` installed by `make setup` (pinned below the compromised 1.82.7/1.82.8 releases); `aegis-litellm` streams and returns usage through the gateway; e2e test; the gateway still runs without it | pass |

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
| Home | **100** | **91** | 100 | 100 | 0.6 s / 0 ms / 0.004 |
| Architecture | 98 | 81 | 100 | 100 | 1.1 s / 0 ms / 0 |
| Control Room | 99 | 82 | 100 | 100 | 0.9 s / 0 ms / 0.001 |

**Home ≥ 85: pass on desktop and on mobile** (simulated slow 4G and 4× CPU throttling; mobile LCP
3.0 s). The previous build scored 91/69 on Home. Three fixes made the difference:

- **Chunking.** Manual chunk rules had swept small shared helpers into the three.js and React Flow
  chunks, so Home preloaded 340 KB it never used. The rules were removed; every heavy library was
  already behind a lazy import, and the automatic split keeps them off the first page.
- **Deferred extras.** The docked 3D robot and the background preloading of other pages now start
  on the visitor's first interaction (or after 6–8 s), not during the first paint.
- **Charts.** ECharts loads when a chart mounts, so a dashboard's text paints before it arrives.

Mobile Architecture and Control Room stay below 85 (their LCP is 4.3 s: React Flow, the 3D galaxy
and the charts are what those pages are for).

## Frame times (Chromium on the Mac GPU, `make perf`)

| Scenario | fps | p95 frame | janky frames |
| --- | --- | --- | --- |
| Home: hero idle | 120 | 9.0 ms | 0 / 479 |
| Home: scroll through the neuron dive | 118 | 9.1 ms | 1 / 628 |
| Home: Meet AEGIS + docked robot | 120 | 8.6 ms | 0 / 478 |
| Architecture (detailed): burst traffic | 80 | 25.0 ms | 12 / 315 |
| Architecture: 3D galaxy + traffic | 61 | 33.5 ms | 15 / 244 |
| Control Room: idle | 117 | 9.0 ms | 2 / 466 |

All scenarios are at or above 60 fps. These numbers vary by ±20 fps between runs on this machine
because other apps (Teams, Slack, WindowServer, endpoint security) use 20–45% CPU in the background.

The Architecture views drop below 120 Hz only while the local models are answering:
- Six requests answered from cache move as many packets and hold 116 fps.
- Six answered by the models drop to about 60–80 fps.
- The Docs page holds 118 fps through the same model burst.

A CPU profile shows the main thread idle 85% of the time during a burst, so the remaining cost is
in rendering, not JavaScript. Two hotspots the profiles did find were fixed:
- SVG path lookups for packet positions went from 310 ms to 8 ms per burst, by sampling each path once.
- Chart colour tokens are no longer re-read from computed CSS on every update (128 ms per 5 s at idle).

A same-session A/B showed the spinning node borders were not the bottleneck. They now run only on
active nodes anyway, and the planet pulse uses transform and opacity only.

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

## Found in a later health check: breakers stuck after laptop sleep

After the Mac slept overnight, all three backends showed "degraded" with half-open breakers,
although every model was fine. The cause had two parts:
- While the Mac slept, every health probe timed out at once, which opened every breaker.
- Half-open breakers then waited forever for a live request that never came.

Two fixes:
- Probe failures in the 10 s after a host pause (detected when the wall clock jumps past the check
  interval) are not counted.
- When a breaker is half-open and its backend idle, the health monitor sends the one live probe
  request itself.

Three tests cover this; the pause test fails without the fix. Checked on the real system: freezing
MLX, then reviving it with no traffic, closed the breaker on its own after 31 s (30 s cool-down plus
one probe). The backend suite now has 64 tests.

## Found in the pre-demo check (2026-11-04)

A full rehearsal of the demo on the live system found three problems, all fixed:
- **The PII guardrail hid the project's own words.** "Mac", "Redis Stack", "Lua", "OpenAI",
  "Playground" and "Chaos Lab" were tagged as people. The assistant's doc excerpts pass through
  the gateway, so it answered "AEGIS runs on a single <PERSON>". These words are now known
  non-names; a real name beside them is still redacted.
- **The assistant could not say what just happened.** Asked "What just happened to the primary?"
  after a crash, it replied "[1]": the demo script quotes that exact question, so the 3B model
  cited it. The demo script is no longer indexed, and live answers now start with a plain
  timeline of the last 10 minutes of chaos actions and breaker changes.
- **The crash scenario's last line could be wrong.** The health monitor sometimes probes the
  revived primary first, and a hedge can let the fallback answer the scenario's own request. The
  line said "Probe served by ollama-llama". It now reports only what is certain.

- **A name and email typed casually got through.** "my name is vinay and vinaysharma@gmail is my
  email" reached the model unchanged, and the model completed the address. The email pattern
  needed a ".com"-style ending, and names had to be capitalised (spaCy also misses a lone first
  name). An address without its ending is now redacted. So is a name after "my name is" or
  "call me", in any case. After "I am" or "this is", only a known given name counts, so
  "I am fine" stays untouched.

Fourteen tests were added. The backend suite now has 78 tests, and all 16 e2e tests pass.

## Production pass (2026-11-05)

The project was audited against the capstone production-readiness checklist and the earlier
weeks' review feedback; the full assessment is docs/PRODUCTION_READINESS.md and the findings are
in docs/PEER_AUDIT.md. Everything below was measured on the real system.

| Check | Result |
| --- | --- |
| Real vLLM (vllm-metal 0.32.0, V1 engine) through the gateway | served by `vllm-metal`, 31 tokens in 0.8 s direct |
| Real LiteLLM proxy (1.80.0) through the gateway | served by `litellm-proxy`; 225 MB resident |
| Llama Guard 3 1B | blocks "build a pipe bomb" (S1, P=0.986); allows "kill a Python process" (P=0.69, borderline, recorded); 60-150 ms warm |
| Output redaction | an invented contact card came back as `<EMAIL_ADDRESS>` and `<PHONE_NUMBER>` |
| WebSocket chat with cancel | cancelled after 5 chunks; the upstream stream was cut |
| Langfuse 4.67.0 (self-hosted) | every stage a span, the model call a generation with usage and cost, the hedge loser a cancelled generation, the tenant as user (docs/evidence/) |
| Prometheus `/metrics` | 13 metric families; breaker states and slots per backend |
| Secure defaults | production mode: 401 anonymous, 403 tenant, 200 admin; chaos refused |
| Shutdown with the site connected | the old gateway exited in about 2 s (it used to hang) |
| Load test, 12 concurrent | 126 req/s on cache hits (p50 49 ms); mixed: 8.5 req/s, 102 of 120 without a model call, 0 errors |
| Evaluation gate | semantic cache precision 1.0 / recall 1.0; PII 0.938 / 0 FP; injection 0.944 / 0 FP; output 1.0 / 0; Llama Guard 1.0 / 0 |
| Container memory | the docs index peaked at 1.49 GB (OOM in a 2 GB VM); batched, 444 MB; the container runs at about 720 MB |

## Not done or changed from the spec

- DeBERTa injection classifier: dropped for the 16 GB budget; a regex check stands in, as documented.
- The Architecture Overview and Trace tabs were removed at the user's request; their content now
  lives in the Detailed view.
- Mobile Lighthouse on Architecture and Control Room (81–82; the target is for Home, which is 91).

## Final audit against the original spec (deep pass)

Each phase was re-read against the build prompt. Four gaps were found and closed:
- **LiteLLM was not installed**, so the optional adapter showed "disabled". It is now installed by
  `make setup`, verified with streaming and non-streaming requests, and covered by an e2e test.
- **The Chaos Lab had live charts but no mini architecture graph.** It now has a "Live path" graph.
  It shows requests, then the gateway, the router and each backend, with real health, breaker
  countdowns and slot use. Every real event sends a packet coloured by outcome. An e2e test kills
  the primary and checks the graph shows its breaker open.
- **The Vercel Web Interface Guidelines audit had no recorded result.** It was run against the
  latest rules:
  - No `transition: all`, removed outlines, zoom lock, clickable divs, autofocus or blocked paste.
  - Images are sized and have alt text.
  - The theme colour and `color-scheme` are set.
  - Two findings were fixed: the Traces filter now lives in the URL (`?outcome=`), and the Control
    Room `<select>` has a solid background so native options stay readable.
  - Dates and numbers use fixed `en-GB`/`en-US` formats on purpose, so demo screenshots and the
    docs match (see DECISIONS).
- **Screenshots at 1280 px and 768 px.** These were added to the sweep. At 768 px the docked robot
  covered the request table, so the compact robot now applies below 1024 px.

Also fixed on the user's report: **switching tabs felt slow.** The old page waited 0.3 s to fade
out, the new page flashed blank for about 0.5 s while it loaded, and the headline appeared last
(about 0.75 s). Now:
- pages are preloaded after the first interaction;
- the loading boundary keeps the current page on screen until the next is ready;
- shimmered titles animate as one piece, so they are visible from the first frame (gradient text
  cannot paint words that are mid-animation).

Every tab is fully shown in 0.1–0.3 s, never blank (`tools/tabs.mjs`).
