# Decisions

Each entry: what was decided, why, and what was rejected.

## The core path uses raw httpx; LiteLLM is a backend, twice

The point of this gateway is to show failover, hedging, breakers and caching working. LiteLLM's
router has its own retries, fallbacks and cooldowns; letting it own the path would hide exactly the
behaviour the demo exists to show, and two retry layers would multiply attempts. So AEGIS owns
reliability and LiteLLM appears where it is strongest, provider translation, in two forms:
- **the real LiteLLM proxy server** (`litellm --config infra/litellm/config.yaml` on :4000),
  model `aegis-litellm-proxy`. Its `num_retries` is 0; its master key comes from the
  environment (`api_key_env`), never the YAML. Adding a cloud provider is one `model_list` entry.
- **the LiteLLM SDK** inside the gateway process (`kind: litellm`), model `aegis-litellm`.

Both run inside AEGIS chains, so slots, breakers and hedging wrap them like any backend. LiteLLM
is pinned to 1.80.0 (see below) and runs in its own environment (`make setup-serving`).

## Langfuse and Jaeger from one OTLP export

Spans go to Jaeger always and to Langfuse when `AEGIS_LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are set:
one OTLP/HTTP exporter each (Langfuse's endpoint is `{host}/api/public/otel` with Basic auth), so a
slow Langfuse never delays Jaeger. No SDK: the spans carry Langfuse's own attributes
(`langfuse.trace.name`, `langfuse.user.id` = tenant, tags, `langfuse.observation.type`), so the
model call arrives as a `generation` with model, usage and estimated cost, and the request root as
a plain span, so usage is counted once.

Self-hosted Langfuse v4 needs Postgres, ClickHouse, Redis and MinIO, about 3-4 GB, which does not
fit permanently beside three local models on 16 GB. So it is an optional stack
(`infra/langfuse/docker-compose.yml`, pinned 4.67.0, headless init, `make langfuse-up`), verified
end to end on 2026-11-05 (screenshots in docs/evidence/), and Jaeger stays the always-on viewer.
Prompt and answer text is never exported to either; traces carry counts.

## vLLM on a Mac: the real engine through vllm-metal

vLLM is built for NVIDIA GPUs, but the official vllm-metal plugin runs its V1 engine on Apple
Silicon with MLX doing the maths (paged attention, chunked prefill, continuous batching). It is a
real `vllm serve` on :8090 with a 0.5B model, so it fits beside the 3B MLX primary and the 3B
Ollama fallback; its backend has 8 slots because continuous batching lets many requests share a
forward pass (the MLX server has 1). Pinned wheels (vLLM 0.32.0, vllm-metal 0.32.0, two weeks old
at pinning) in their own environment. A GPU box would be one more YAML entry (`vllm-gpu`).

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

## Loading: no manual chunks, extras after the first interaction

three.js, ECharts, React Flow and the markdown renderer each sit behind a lazy import, so the
bundler's automatic split already keeps them off the first page. Hand-written chunk rules were
removed: they swept small shared helpers into the three.js and React Flow chunks, which Home then
had to preload (340 KB it never used). The docked 3D robot and the preloading of the other pages
start on the visitor's first interaction, or after 6–8 s if there is none, so they never compete
with the first paint. Charts load ECharts when they mount. Home went from 69 to 91 on mobile
Lighthouse, and tabs still open instantly because their code is fetched before it is needed.

## Page transitions: the old page stays until the new one is ready

The loading boundary sits outside the keyed page element, so a tab switch keeps the current page
on screen instead of flashing an empty placeholder. The new page settles in 180 ms with no exit
animation to wait for. Gradient-filled titles animate as one piece, because `background-clip: text`
cannot paint child words while they are mid-transform.

## LiteLLM installed by default, pinned

`make setup` installs the `litellm` extra so the optional adapter can be demonstrated. It is
pinned to 1.80.0, below the 1.82.7 and 1.82.8 releases compromised in the March 2026 supply-chain
incident. The core path still does not depend on it: without it the backend shows "disabled" and
the gateway runs normally.

## Fixed date and number formats

Times use `en-GB` (24-hour) and counts use `en-US` grouping everywhere, instead of the visitor's
locale. Demo screenshots, the docs and the narrated scenarios then read the same on any machine.
This is a deliberate exception to the Web Interface Guidelines' "use the browser locale" rule.

## Secure by default, convenient in dev

The Week 3 review flagged `AUTH_ENABLED` defaulting to false. Here the default is the safe one:
`AEGIS_ENV=production` unless set. Production requires an admin credential for the control plane,
refuses chaos even if the flag is on, and never trusts loopback (behind a reverse proxy everyone
looks local). Dev mode is an explicit opt-in that `scripts/dev.sh` makes, and the gateway logs a
warning when it is on. Rejected: a separate `AUTH_ENABLED` switch (one more way to be insecure)
and a login flow for the demo site (friction with no security gain on one laptop).

## Public key ids instead of keys

Before, the raw key was the ledger column, the Redis bucket name, the event field and the
`/api/tenants` value, and `/api/config` returned every key and backend credential. Now a key has
a public `id`; the key itself never leaves `security.py`. Existing ledgers are migrated in place.
Keys can be stored as `sha256:` entries, compared in constant time across every entry.

## Llama Guard: thresholded, leader-only, output before caching

Llama Guard 3 1B (~110-150 ms warm) labelled "How do I kill a Python process?" unsafe. Its own
probability of the `unsafe` token separates the cases: false positives 0.50-0.69, real hazards
0.986-1.0 (measured on this Mac). So a prompt is blocked only at P(unsafe) >= 0.9 and a borderline
verdict is allowed and recorded, as the Llama Guard paper suggests. It runs only on a cache miss
and only for the coalescing leader (ten identical requests cost one check), and the output check
runs in the background before the cache write, so an unsafe answer is never replayed and no client
waits for it. Rejected: the 8B model (does not fit), checking output before streaming (it would
remove streaming).

## Output redaction by regex, with a hold-back buffer

A model can write PII it was never given. Presidio per token would cost milliseconds hundreds of
times per answer, so the output guard uses precise patterns for structured values (emails, phone
formats, Luhn-valid cards, known secret formats) and holds back the last word, and any trailing
run of number-like words, until it is complete, so a value split across chunks is still caught.

## A chain retry only when every backend failed

Retrying an open breaker cannot help, and retrying in front of a busy backend adds load. So the
gateway retries the whole chain once, after a full-jitter backoff (0-200 ms), only when every
backend actually failed, and never past the queue deadline. Retries are counted in Prometheus.

## Prometheus from the event bus

The exporter subscribes to the same lifecycle events as the website, so a Grafana panel and the
Control Room cannot disagree. Labels are bounded (model, backend, cache, status, tenant); request
and trace ids never become labels. Gauges that describe the present (breaker state, slots) are
read at scrape time.

## An evaluation gate for a gateway

A gateway writes no answers, so faithfulness is not its metric; its judgements are. Each judgement
has a labelled golden set scored against the real component with a pass threshold, run with the
test suite so CI fails on a regression. The semantic cache gate is exact (precision 1.0), because a
wrong hit serves another question's answer; a missed paraphrase only costs one model call. The
first run found three injection framings the regex missed; a held-out set written afterwards keeps
the claim honest.

## WebSocket chat beside SSE

SSE stays the OpenAI-compatible default. `/v1/ws/chat` adds what SSE cannot do: the client can
cancel a generation mid-stream, which cancels the upstream call and frees its slot, and many
requests share one connection. Same pipeline, same guardrails, same traces.

## Embedding in batches of 16

The docs index embedded every chunk in one fastembed call (default batch 256): a 1.49 GB peak at
startup, which a 2 GB container did not survive. Batches of 16 peak at 444 MB and take 6.5 s for
300 chunks, at startup only.
