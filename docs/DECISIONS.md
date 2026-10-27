# Decisions

Each entry: what was decided, why, and what was rejected.

## The core path uses raw httpx, not LiteLLM

The point of this gateway is to show failover, hedging, breakers and caching working. LiteLLM's
router has its own retries, fallbacks and cooldowns; letting it own the path would hide exactly the
behaviour the demo exists to show, and two retry layers would fight each other. LiteLLM is still
demonstrated: it is one optional backend (`litellm-ollama` in the YAML, `uv sync --extra litellm`,
pinned version) inside a chain, so AEGIS's slots, breakers and hedging wrap it like any backend.

## Jaeger with OpenTelemetry, not a self-hosted Langfuse

Self-hosted Langfuse v3 needs Postgres, ClickHouse, Redis and MinIO; on a 16 GB Mac already running
two local models that is the difference between fitting and swapping. Jaeger all-in-one is one
container (about 50 MB resident here). The export is standard OTLP over HTTP with the official
`gen_ai.*` semantic conventions, which Langfuse ingests natively through its OTLP endpoint
(`/api/public/otel`), so pointing `AEGIS_OTLP_ENDPOINT` at a Langfuse instance is a configuration
change, not a code change.

## The three corrections to the tournament design

1. **No temperature injection.** The design that won the arena injected `temperature=0` when a client
   omitted it, so that real clients would hit the cache. That silently changes model behaviour for
   every client. Instead the cache keys on the *effective* parameters: whatever the client sent,
   filled in with the first backend's real defaults from the YAML. The demo clients set
   `temperature: 0` explicitly. Test: `test_omitted_temperature_uses_provider_default_not_zero`.
2. **Batched, off-loop ledger writes.** Rows go to a queue; one task writes batches (up to 200 rows
   or every 250 ms) in a worker thread. Test: 500 rows enqueue in under 50 ms and land in at least
   ten batches (`test_ledger_batches_writes_off_the_event_loop`).
3. **Honest money.** Every dollar figure is labelled "est. cloud equivalent": local inference has no
   bill, so the number is the counterfactual cost of the same tokens at the YAML's reference price.
   When a backend omits `usage` the tokens are estimated and flagged ±30%.

## Guardrails adapted from Veritas, without its DeBERTa classifier

Veritas (assignment3) already had a tuned Presidio setup (small spaCy model, a narrowed entity list,
Aadhaar/PAN/any-domain email recognisers, a given-name list, a plausible-name filter) and regex
injection patterns. Those are reused (`backend/aegis/guardrails/`). Its DeBERTa injection classifier
is not: it needs torch, about 1.5 GB resident, which does not fit beside two local models. The regex
layer catches the overt framings; subtler injections are a documented gap. Redaction replaces values
with typed placeholders (`<EMAIL_ADDRESS>`) instead of Veritas's reversible vault: a gateway has no
business storing the values.

## Busy is not broken

A backend that is busy (its slots taken) is skipped after a 2 s wait, not penalised. The first-token
timeout starts only when a slot is held, and health-probe failures count against the breaker only if
the backend has not streamed a token in the last 2 s. `mlx_lm.server` answers `/v1/models` in under
10 ms even mid-generation (measured), so a failed probe really means frozen or gone.

## Hedging uses the live p95 TTFT

The hedge fires when the chosen backend has not produced a first token by its own rolling p95 TTFT
(last 40 requests, minimum 250 ms, 2.5 s until it has five samples). A fixed delay would either hedge
everything (wasting a slot on the 2-slot fallback) or never fire. Hedging only uses a backend with a
free slot right now: it never waits.

## No mid-stream failover

Once tokens are sent they cannot be unsent; stitching a second model's continuation onto the first's
partial answer produces text neither model would have written. A backend that dies mid-stream ends the
response with an SSE `error` event; the answer is not cached.

## Semantic threshold 0.95

Measured with bge-small on this machine: a near-identical paraphrase scored 0.986, a genuine
rewording ("What is the shipping time to Canada?") scored 0.949, and a different question about
delivery scored 0.932. At 0.95 the cache serves only near-duplicates; lowering it toward 0.93 would
catch rewordings at a real risk of wrong answers. The threshold is in the YAML and adjustable live
(`POST /api/cache/tune`). The semantic partition includes every message except the last, so a
paraphrase never matches across different conversations, tenants, models or parameters.

## Chaos kills are real where the gateway owns the process

The Chaos Lab's "kill" freezes `mlx_lm.server` with `SIGSTOP` (pid in `var/run/mlx.pid`), so requests
really hang and probes really time out; "revive" sends `SIGCONT`. The system Ollama app is not the
gateway's process, so for it "kill" refuses connections at the gateway's edge instead. Every chaos
action auto-reverts (120 s) and the gateway sends `SIGCONT` on shutdown so a model is never left
frozen. Chaos endpoints exist only with `AEGIS_CHAOS_ENABLED=1`.

## Redis Stack in Colima

Redis 8 bundles vector search but the Homebrew build's module support is uncertain; Redis Stack
7.4 has FT.SEARCH with HNSW for certain. Colima with 2 CPUs / 2 GB runs it and Jaeger.
redis-py 8 returns FT.SEARCH and FT.INFO as dicts rather than flat lists; `cache._first_result`
handles both shapes (this was a real bug found in the first live test).

## Design: the brand wins over three Taste Skill house rules

Taste Skill bans glass, glows and violet. This brief is built on the robots' own frosted tablet,
LED light and violet backdrop, so per the skill's "existing brand" exception those three rules yield;
everything else (two-line headlines, no eyebrows, no index numbers, type scale, one accent family,
quiet dashboards, glow only when earned) is kept. After review the palette was narrowed from a
multi-hue look to black space with one cyan/violet accent family, which reads as more professional.

## Images

The robot pictures were supplied by the user and lifted from their backgrounds on-device with
Apple Vision; the 1.20.15 picture is Meet AEGIS. The 1.20.10 picture, offered for the flying
assistant, was not used: it is a watermarked Dreamstime stock preview (watermark across the face,
captured from a search page), so it is not licensed. A later picture (2.41.50) was
tried for the chatbot, then replaced at the user's request by an original model. The assistant is
now a 3D robot built in three.js (`scenes/Robot3D.tsx`), styled after a reference the user showed
(a dark-visored sentinel) and, at their request, finished in white. Building it lets the eye,
hands, thruster and pad animate and react to the pointer.

## Site components adapted

- `PerspectiveTunnelHero` and `TiltedGridHero` (21st.dev) were pasted by the user. Both were kept
  close to the original, with three changes: they use the colour tokens; they show screenshots of this
  site instead of Unsplash stock (no third-party images, no network dependency); and they respect
  reduced motion. The tunnel no longer hijacks the wheel; it follows normal page scroll.
- The `components/ui` folder and the `cn` helper (clsx + tailwind-merge, the `@/` alias) follow the
  shadcn convention those components expect. `framer-motion` imports became `motion/react`.
- The Architecture page has two tabs, not four: the user asked for only the 3D galaxy and the
  detailed view. Overview and Trace were folded into Detailed (old `?view=` links still resolve).
- Galaxy labels are one DOM layer positioned each frame from the 3D bodies, not drei `Html`:
  drei gives every label its own React root, and under React 19 the first root was lost when the
  canvas's event target connected (found by the e2e test).

## Performance budget

Frame times are measured in Chromium on the Mac GPU (`make perf`). Two findings changed the code:
full-screen blend-mode layers (grain, cursor glow) and continuously animated conic borders and
box-shadows forced repaints, so they were made static or composited; the architecture graph
re-rendered every node on each topology poll, so nodes are memoised on what they display.
