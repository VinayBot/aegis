# Learning PRAHARI: a guided tour of the whole project

This guide explains PRAHARI from the ground up: what problem it solves, how a request travels through
it, what every tab of the website shows, which technologies were used, and why. Each idea is
explained first in plain words, then shown in this codebase, then given a way to try it yourself.

Read it in order the first time. Later, use the table of contents to jump to a part.

1. [The big picture](#1-the-big-picture)
2. [The machine: what runs where](#2-the-machine-what-runs-where)
3. [The journey of one request (the heart of the project)](#3-the-journey-of-one-request)
4. [Reliability: breakers, hedging, failover, "busy is not broken"](#4-reliability-staying-up-when-models-misbehave)
5. [Seeing inside: events, metrics, traces](#5-seeing-inside-events-metrics-and-traces)
6. [The website, tab by tab](#6-the-website-tab-by-tab)
7. [The PRAHARI chatbot](#7-the-prahari-chatbot)
8. [The tech stack, and why each piece](#8-the-tech-stack-and-why-each-piece)
9. [How the frontend is built](#9-how-the-frontend-is-built)
10. [Testing: how we know it works](#10-testing-how-we-know-it-works)
11. [Performance lessons learned the hard way](#11-performance-lessons-learned-the-hard-way)
12. [Real bugs found, and what each one teaches](#12-real-bugs-found-and-what-each-teaches)
13. [How to read the code (a suggested order)](#13-how-to-read-the-code)
14. [Exercises](#14-exercises)
15. [Glossary](#15-glossary)

---

## 1. The big picture

### The problem

Imagine a company where many apps call AI models: a support bot, a search tool, an internal
assistant. If every app talks to the models directly, then:

- nobody knows how much each app spends;
- one app can flood the model and slow down everyone else;
- if a model crashes, every app breaks at once;
- the same question gets answered (and paid for) thousands of times;
- private data (emails, phone numbers) can leak into prompts;
- when something goes wrong, nobody can see where.

### The solution: a gateway

An **AI gateway** is one front door that every app goes through. Apps think they are talking to a
normal OpenAI-style API. Behind that door, the gateway:

| Job | In plain words |
| --- | --- |
| Authentication | "Who are you?" Every caller has an API key. |
| Guardrails | "Is this prompt safe?" Hides personal data, blocks prompt injection. |
| Rate limiting | "You're going too fast." A per-key token budget per minute. |
| Budgets | "You've spent enough today." A soft warning and a hard stop. |
| Caching | "I answered that already." Replays old answers instantly. |
| Coalescing | "Ten people asked the same thing at once? I'll ask the model once." |
| Routing | "Which model is healthy and free right now?" |
| Hedging and failover | "That model is slow or broken; try another one." |
| Observability | "Show me exactly what happened to every request." |

**PRAHARI** is that gateway, built for real, running on one Mac with two local AI models. The website
is a live window into it: every number, light and chart on it comes from the running system.

### The one rule of this project

> This is a real system, not a mock.

No number on the website is invented. If the gateway is offline, the site says so with a
"REPLAY: backend offline" banner and plays back traffic that was really recorded earlier. In the
frontend, `Math.random()` is banned in data paths: an ESLint rule and a unit test enforce it.

---

## 2. The machine: what runs where

`make dev` starts all of this on one Mac:

```
 Browser (React site)  :5173  ──────────────┐
   │  HTTP + WebSocket                      │ (Vite dev server proxies /api, /v1, /ws)
   ▼                                        ▼
 PRAHARI gateway (FastAPI, Python)  :8000
   │            │              │                     │
   │            │              │ OTLP traces          │ background writes
   ▼            ▼              ▼                     ▼
 MLX server   Ollama        Jaeger :16686          SQLite ledger
 :8081        :11434        (trace viewer)         (usage + costs)
 Qwen2.5-3B   Llama-3.2-3B
 (primary)    (fallback)
                 ▲
                 └─ also reached through LiteLLM (optional adapter)

 Redis Stack (in Docker via Colima): exact cache, vector index for the
 semantic cache, rate-limit buckets, budgets
 fastembed bge-small (inside the gateway process): turns text into vectors
```

| Piece | What it is | Why it's here |
| --- | --- | --- |
| **FastAPI gateway** | A Python web server (async) | The brain. Every request goes through it. |
| **MLX + Qwen2.5-3B (4-bit)** | Apple's ML framework serving a small model on the Mac GPU | The primary model, fast on Apple Silicon. It has **1 slot** (one request at a time). |
| **Ollama + Llama-3.2-3B** | A popular local model server | The fallback model, with **2 slots**. |
| **LiteLLM** | A library that speaks to 100+ model providers | Shown as one optional backend, to prove the gateway can use it without depending on it. |
| **Redis Stack** | An in-memory database with vector search | Fast shared state: caches, rate limits, budgets. |
| **bge-small (fastembed)** | A small embedding model (384 numbers per text) | Powers "is this question similar to an old one?" |
| **Jaeger** | A tracing UI | Shows the timeline of every request, stage by stage. |
| **SQLite** | A file-based database | The ledger: who used how many tokens, what it would have cost. |

> **Learning note: "local" models.** These are small (3 billion parameter) models compressed to
> 4-bit so they fit in a 16 GB Mac. They're slower and less accurate than big cloud models, which is
> exactly why a gateway's caching, routing and failover matter so much here.

---

## 3. The journey of one request

This is the most important section. Follow one chat request from start to finish. The code is
`backend/prahari/pipeline.py`, and every step below is its own **span** (a timed box) in the trace.

```
client ─► auth ─► guardrails ─► rate limit ─► budget ─► exact cache ─► semantic cache
                                                            │ hit            │ hit
                                                            ▼                ▼
                                                        replay answer   replay answer
          ─► coalescer ─► router (slots, breakers) ─► hedger ─► model ─► stream relay ─► client
                                                                              │
                                                                   ledger + cache write
                                                                   (background queue)
```

The website groups these into six phases: **Check** (auth, guardrails), **Allow** (rate limit,
budget), **Remember** (caches, coalescer), **Choose** (router, hedger), **Think** (the model) and
**Deliver** (relay, ledger).

### Step 1: Authentication

- **Idea:** every caller sends `Authorization: Bearer <key>`. The key maps to a **tenant** (a team
  or app) with its own limits.
- **Here:** keys live in `backend/config/gateway.yaml`. Examples:
  - `prahari-demo-key` is the demo tenant;
  - `prahari-flood-key` has a tiny limit, so the rate-limit demo can trip it.
- **Try it:** send a request with a wrong key and you get `401`.

### Step 2: Guardrails (privacy and safety)

- **PII redaction:** before any model, cache or embedder sees the prompt, personal data is replaced
  with placeholders. "Email priya@example.com" becomes "Email `<EMAIL_ADDRESS>`".
  - **How:** Microsoft **Presidio**, plus spaCy's `en_core_web_sm` language model, which recognises
    names. Custom recognisers handle email, a given-names list, and Indian Aadhaar and PAN numbers.
  - **Where:** `backend/prahari/guardrails/pii.py` (adapted from the earlier Veritas project).
  - **Lesson from testing:** spaCy thought "Redis" and "Ollama" were people's names. A list of
    software names fixed that, while a real name next to "Redis" is still hidden.
- **Prompt injection:** "Ignore all previous instructions…" is blocked before any model runs
  (`guardrails/injection.py`, pattern rules).
- **Why it matters:** the trace records only *counts* ("1 email redacted"), never the values. Privacy
  includes your own logs.

### Step 3: Rate limiting, a token bucket in Redis

- **Idea:** picture a bucket that holds N tokens and refills at a steady rate. Each request takes
  tokens out. If the bucket is empty, you get **429 Too Many Requests** with a `Retry-After` header
  telling you when to come back.
- **Why tokens, not requests?** A 5-word prompt and a 5,000-word prompt are not the same cost. This
  limiter counts **tokens per minute (TPM)**.
- **Reserve, then refund:** before the model answers, the gateway doesn't know how many tokens the
  reply will use, so it **reserves** the maximum. When the answer finishes, it **refunds** the unused
  part.
- **Why a Lua script?** Redis runs a Lua script **atomically**: read the bucket, refill, subtract
  and save all happen as one step, so two simultaneous requests can never both take the last token.
  See the script at the top of `backend/prahari/ratelimit.py`.
- **If Redis is down:** it falls back to an in-process bucket. The system degrades; it does not die.

### Step 4: Budgets

- **Idea:** each tenant has a **soft** limit (you get a warning header) and a **hard** limit (blocked
  with `402`).
- **Cost honesty:** local models have no bill. PRAHARI therefore prices every request at a reference
  cloud price (gpt-4o-mini list price) and labels it **"est. cloud equivalent"** everywhere. When a
  model doesn't report token usage, the estimate is flagged ±30%. This was one of the three
  mandatory corrections to the original design.

### Step 5: The exact cache

- **Idea:** if the *same* question with the *same* settings was answered before, replay the answer.
- **The cache key** is a hash of (tenant, model, all messages, effective parameters). Parameters
  matter: the same question at temperature 0 and at 0.8 should give different answers, so they are
  different entries.
- **Correction 1 (important):** if the client doesn't send a temperature, PRAHARI does *not* invent
  `temperature=0`. It caches on the provider's real default instead. Silently changing a client's
  request would change its answers.
- **Replay as a stream:** a cached answer is sent back as server-sent events, so clients can't tell
  the difference, except that it's about 7 ms instead of seconds.
- The response header says what happened: `x-cache: hit | semantic | miss | bypass | coalesced`.
- `Cache-Control: no-cache` skips the cache for that request.

### Step 6: The semantic cache (meaning, not spelling)

This is the most "AI" part of the gateway.

- **Idea:** "How does failover work?" and "how does failover work" are different strings but the
  same question. An exact cache misses them; a **semantic** cache catches them.
- **How:**
  1. Turn the last user message into an **embedding**, a list of 384 numbers that captures its
     meaning (bge-small model).
  2. Look in a **vector index** for the closest stored question (Redis `FT.SEARCH` with an **HNSW**
     index and **cosine similarity**).
  3. If similarity ≥ **0.95**, replay that answer.
- **Partitioning (safety):** vectors are only compared within the same partition: tenant + model +
  parameters + all earlier messages. Tenant A never gets tenant B's answer, and a question in a
  different conversation context doesn't match.
- **Only short prompts:** messages over 200 tokens (like long RAG prompts full of pasted documents)
  get exact matching only, because two long prompts can look "similar" while needing different
  answers.
- **Real numbers measured:** a paraphrase at 0.986 hits; one at 0.949 misses.
- **The embedder runs inside the gateway**, not in a model server, so a model crash can't take the
  cache down too.

> **Learning note: what is cosine similarity?** Think of each embedding as an arrow in
> 384-dimensional space. Cosine similarity measures the angle between two arrows: 1.0 means the
> same direction (same meaning), 0 means unrelated. **HNSW** (Hierarchical Navigable Small World) is
> a graph structure that finds the nearest arrows quickly without comparing against every stored one.

### Step 7: Coalescing ("singleflight")

- **Idea:** if 10 identical requests arrive *at the same moment*, none of them can hit the cache yet,
  because no answer exists. Without coalescing, the model would generate the same answer 10 times.
- **How:** the first request becomes the **leader** and calls the model. The other nine become
  **followers**: they wait on the leader's "flight", and the leader's stream is copied to all of
  them. Result: **1 upstream call, 9 coalesced**.
- **Where:** `backend/prahari/coalesce.py` (`Flight`, `Coalescer`).
- **Try it:** Chaos Lab → "Duplicate storm", or Playground → "Send this 10× at once".

### Step 8: Routing (choosing a model)

- **Slots:** each backend can only handle so many requests at once (MLX 4, Ollama 2, vLLM 8; set from a measurement, `make bench-serving`). The router
  tries backends in the YAML order (primary first), skipping any whose **circuit breaker** is open.
  It waits up to **2 s** for a free slot, then moves on to the next backend.
- **Nothing free for 10 s:** `503` with `Retry-After`.
- See section 4 for breakers, hedging and failover.

### Step 9: The model call and the stream relay

- The gateway calls the backend with **httpx**, using the OpenAI-compatible API every local server
  speaks (`backends/openai_compat.py`). Adding a new server such as vLLM is **one YAML entry**.
- Answers **stream** back token by token as **SSE** (server-sent events): the gateway relays each
  chunk the moment it arrives, which is why the reply appears word by word.
- `include_usage` is forced on so the model reports exact token counts at the end.
- **If a model dies mid-answer:** you can't "un-send" tokens the user already saw, so there is no
  mid-stream failover. The gateway sends an SSE `error` event, estimates the tokens, and does
  **not** cache the broken answer.

### Step 10: Record (in the background)

- The ledger write (SQLite) and the cache write (Redis) go into **background queues**, never the
  request path. The user never waits for bookkeeping.
- **Correction 2:** ledger writes are **batched**, so a burst of 100 requests doesn't do 100 tiny
  disk writes that block the event loop.

---

## 4. Reliability: staying up when models misbehave

### The circuit breaker

Named after the electrical switch in your house. It has three states:

```
          3 consecutive errors, or 1 first-token timeout
 CLOSED ─────────────────────────────────────────────────► OPEN
 (normal)                                                  (skip this backend for 30 s)
    ▲                                                          │
    │ probe succeeds                                           │ 30 s pass
    │                                                          ▼
    └──────────────────────────────── HALF-OPEN ◄──────────────┘
                     probe fails → back to OPEN    (exactly one live request "probes" it)
```

**Why?** Without a breaker, every request would wait the full timeout on a dead model before
failing over. With it, after a few failures, traffic skips the broken model instantly. Then, carefully,
one request checks whether it has recovered. Code: `backend/prahari/breaker.py`.

### Failover

If the chosen backend fails **before the first token** (connection refused, a 5xx, a 429, an unknown
model, or no first token within 15 s + 1 s per 1,000 prompt tokens), the router tries the next
backend. The user just sees an answer, possibly a little later.

### Hedging (racing two models)

- **Idea:** sometimes a model isn't broken, just slow right now. If the primary hasn't produced its
  first token by its usual worst-case time, its **rolling p95 time-to-first-token (TTFT)**, the
  gateway sends the *same* request to another free backend as well. Whichever streams first wins;
  the loser is cancelled and its slot released.
- **p95** means "95% of recent requests were faster than this". It's measured live (never sooner
  than 250 ms), so hedging adapts to how the model is actually behaving.
- **Trade-off:** hedging spends extra compute to cut tail latency. That's why it fires only at p95
  and not immediately.

### "Busy is not broken"

A subtle but important rule. If MLX is busy (its one slot is taken), that is **not** a failure:
- the request simply moves on after the 2 s slot wait;
- the first-token timeout clock starts only **after** a slot is acquired;
- a health-probe failure counts against the breaker only if the backend isn't currently streaming.

Without this rule, heavy traffic would make the breaker think a healthy model was broken.

### Chaos engineering (breaking things on purpose)

The **Chaos Lab** really breaks the system:
- **Kill:** the MLX process is frozen with `SIGSTOP`, the same signal as pressing Ctrl+Z on a
  program. It doesn't crash; it just stops answering, which is realistic and nasty.
- **Latency:** delay every response from a backend by N ms.
- **Error rate:** make N% of calls fail.

Every action is logged, reversible, and **auto-reverts after 120 s**. The chaos endpoints only exist
when `PRAHARI_CHAOS_ENABLED=1` (`make dev` sets it). On a real deployment they would be off.

---

## 5. Seeing inside: events, metrics and traces

Three kinds of visibility, each with a different job:

| | What | Built with | Used by |
| --- | --- | --- | --- |
| **Events** | "Request X entered stage Y", "cache hit", "breaker opened" | An in-process event bus pushed over a **WebSocket** (`/ws/events`) | The website's live animations |
| **Metrics** | Numbers over time: requests per second, p95 latency, hit rate | `metrics.py`, served at `/api/metrics` | Charts and KPIs |
| **Traces** | The full timeline of one request, every stage as a timed span | **OpenTelemetry** → **Jaeger** | Debugging one request |

### Why a WebSocket?

Normal HTTP is "ask, then get an answer". A WebSocket is a phone line that stays open, so the server
can push every event the moment it happens. That's how a light moves on the architecture map
exactly when a real request crosses that stage.

### OpenTelemetry and the `gen_ai.*` conventions

OpenTelemetry (OTel) is the industry standard for traces. PRAHARI creates one **span** per stage and
uses the official **GenAI semantic conventions** (attribute names like `gen_ai.request.model` and
`gen_ai.usage.input_tokens`). Because it's standard, the same traces could be sent to Langfuse,
Grafana or Datadog with only a config change.

> **Why Jaeger and not Langfuse?** Self-hosted Langfuse needs Postgres, ClickHouse, Redis and MinIO,
> which is too heavy for a 16 GB Mac already running two models. Jaeger is one small container. The
> traces are OTLP-standard, so Langfuse could ingest them later. (docs/DECISIONS.md)

### The control-plane API

The website never hard-codes the system's shape. It asks:
- `GET /api/health`: real probes of every component;
- `GET /api/architecture`: the live topology (nodes, edges, status, latency, counters). Add a
  backend to the YAML and the diagram changes with no frontend edit;
- `GET /api/metrics`, `/api/tenants`, `/api/requests`, `/api/traces`, `/api/docs`, `/api/chaos`;
- `POST /api/playground/*` and `/api/scenarios/*`: load tests and guided scenarios.

The full list is in `docs/API.md`, or browse it interactively at `http://127.0.0.1:8000/api/swagger`.

---

## 6. The website, tab by tab

The top navigation: **Home · Architecture · Control Room · Chaos Lab · Playground · Traces · Docs**,
plus the 3D robot in the bottom-right corner on every page.

Things every page shares (`components/Shell.tsx`):
- **The galaxy sky:** a WebGL shader of stars and a faint Milky Way (`components/fx/Aurora.tsx`). It
  pulses softly with each real request and tints red at the edges while a breaker is open.
- **The event ticker** under the nav: a running line of real events.
- **The "Live" chip:** shows whether the WebSocket is connected; it turns into the replay banner when
  the gateway is offline.
- **Pause motion:** stops all animation (remembered per browser); the OS "reduce motion" setting
  does the same.

### Home: "Enter the brain"

1. **Hero.** The round robot floats with a gentle bob and tilts toward your cursor.
   - Its face is a live LED display that blinks and reacts.
   - The glass tablet in its hand shows the gateway's **real requests per second**.
2. **The dive.** As you scroll, the camera pushes into the robot's face screen, which dissolves into a
   3D field of **neurons** (cell bodies, dendrites, axons, firing pulses), built in three.js
   (`scenes/Brain.tsx`). You pass five "brain regions", each one a real subsystem with a live number:
   - **The Gate:** auth and guardrails;
   - **Memory:** the exact and semantic caches;
   - **The Hive:** coalescing;
   - **The Reflexes:** breakers, failover and hedging;
   - **The Ledger:** tokens, budgets and savings.
3. **The tunnel.** A scroll-driven 3D tunnel of screenshots of the site's own pages; click one to go
   there (`components/ui/perspective-tunnel-hero.tsx`).
4. **Proof strip.** Live counters: requests served, cache hit rate, estimated savings and breaker
   trips survived.
5. **Meet PRAHARI.** The standing robot: its eyes follow your pointer, a scan beam passes over it, and a
   speech bubble invites you to ask it anything.

With reduced motion, the dive becomes a vertical list of static cards with the same live numbers.

### Architecture: "The system, running live"

It has two views, chosen with the tabs at the top.

**3D Galaxy** (`scenes/Galaxy3D.tsx`):
- The **core** star is the gateway.
- The **six planets** orbiting it are the six phases (Check, Allow, Remember, Choose, Think, Deliver).
- The **moons** around "Think" are the model backends, coloured by health.
- Every real request is a **comet** flying planet to planet as its events arrive:
  - a violet comet is a cache answer that jumps from Remember straight to Deliver;
  - a red burst is a rejection;
  - amber is a hedge or failover.
- Drag to orbit the camera.

**Detailed** (React Flow graph):
- Every component is a node: Client → Auth → Guardrails → Rate limit → Budget → Exact cache →
  Semantic cache (+ embedder) → Coalescer → Router → Hedger → MLX / Ollama / LiteLLM → Relay →
  Ledger → SQLite, plus Redis, Jaeger and the chaos controller as side systems.
- **Packets** travel along the edges for real events only. Their speed reflects real latency (slow
  stages crawl). Colour shows the outcome:
  - cyan: answered normally;
  - violet: from cache or shared;
  - amber: hedged or rerouted;
  - red: rejected.
- **Breaker visuals:**
  - an open breaker shows as a broken, flickering link with a 30 s countdown;
  - half-open sends a single probe packet;
  - closing snaps back with a flash.
- **Hover a node** to dim everything except its upstream and downstream path. **Click (or tap) a
  node** to open a drawer with a plain-English description, live config, p50/p95, counters, the last
  10 events, and the source file.

**Around the map:**
- **Health strip:** pills for MLX, Ollama, Redis, Jaeger, the embedder, LiteLLM and the WebSocket.
  Click one for its raw probe output.
- **Journey strip:** the six phases, each with its last-minute count.
- **Follow requests:** the camera follows the active request.
- **Run self-check:** a real end-to-end probe (tiny embed, cache lookup, one-token generation), with
  per-stage timings drawn on the graph.
- **Export:** PNG, SVG or topology JSON.
- **Live requests list:** click one to **replay its journey in slow motion**, step by step with each
  span's timing, plus a Jaeger link.
- **Send test request:** a prompt box at the bottom; fire it and watch it travel.

If the WebSocket disconnects, **nothing moves**. No fake motion, ever.

### Control Room (the dashboard)

- **Mission band:**
  - gauges for system health (for example 8/8 components up), share answered from cache, and
    first-token p95 against its budget;
  - a heartbeat line with one beat per request.
- **KPI cards:** requests per second, TTFT p95, cache hit rate and estimated savings, each with a
  sparkline and the change from the previous minute.
- **Charts** (ECharts, each with a "Show as table" fallback):
  - latency by stage;
  - cache results;
  - tokens per minute against the rate limit;
  - a breaker state timeline per backend;
  - hedging and failover;
  - the traffic split between backends.
- **Per-tenant table:** tokens used, a budget bar with soft and hard markers, 429 count, savings.
- **Live request feed:** click a row to replay that request on the Architecture page.
- **The small robot** sits in the corner. Its face shows the system's mood, driven by real health:
  content when healthy, concerned when degraded, alarmed when a breaker is open.

### Chaos Lab

- **Per backend:**
  - a big **kill switch** (SIGSTOP);
  - a **latency** slider;
  - an **error-rate** slider;
  - an auto-revert countdown.
- **Guided scenarios** (one click each, narrated step by step in "What is happening"):
  - *Primary crashes:* freeze MLX, watch traffic hedge to Ollama, the breaker open, requests skip MLX,
    then the revive and one probe that heals it;
  - *Traffic burst:* 12 requests at once; slots fill and the overflow spills to the fallback;
  - *Slow primary → hedging:* add 4 s of latency and watch the hedge race;
  - *Duplicate storm → coalescing:* 10 identical requests, 1 upstream call;
  - *Rate-limit flood:* 8 requests on a small key, 4 rejected with 429 and Retry-After;
  - *PII in prompt:* an email, a phone number and a name redacted before any model sees them.
- **Live path graph:** a mini architecture graph (requests → gateway → router → each backend) with
  real health, breaker countdowns, slots in use and a dot per real event.
- **Live charts:** TTFT p95, and model calls per backend.
- **Action log** and **Revert everything**.

### Playground

Send requests by hand.
- **Choose:** the prompt, the model, the tenant key, the temperature, and whether to bypass the cache.
- **The answer streams in**, with a **mini trace** beside it showing:
  - which stages ran and how long each took;
  - the cache result;
  - which backend answered;
  - the token count and the estimated savings.
- **Send this 10× at once:** demonstrates coalescing (fired from the gateway, because a browser only
  opens 6 streams to one site at a time).
- **Rephrase and resend:** slightly rewords the prompt to demonstrate the semantic cache.

### Traces

- A list of recent requests read from **Jaeger's query API**, filterable by outcome: OK, exact hit,
  semantic hit, coalesced, rejected, blocked, mid-stream error. The filter is part of the URL, so you
  can share it.
- Click one to see its **waterfall**: every span as a bar on a timeline. There are also links to
  "Replay on the graph" and "Open in Jaeger".

### Docs

The project's documents, rendered in the site: README, ARCHITECTURE, DECISIONS, API, DEMO_SCRIPT,
VALIDATION_REPORT, SKILLS_REVIEW, DESIGN and PLAN. They're the same files the chatbot answers from.
The header is a curved 3D reel of page screenshots (`components/ui/tilted-grid-hero.tsx`).

### 404

Any unknown address shows "This page is off the map", with a way back.

---

## 7. The PRAHARI chatbot

The white 3D robot in the corner (`scenes/Robot3D.tsx`, built from scratch in three.js):
- it hovers over a glowing pad and suggests questions;
- it perks up and turns toward you on hover;
- it wakes with a hop when clicked;
- press `/` or Ctrl/Cmd+K to open it from anywhere.

### How it answers (a small RAG system)

**RAG** = Retrieval-Augmented Generation: find the relevant text first, then let the model answer
*from that text only*.

1. **Index:** at startup, the project's docs, the YAML config and the API schema are split into chunks
   and embedded with the **same bge-small embedder** the cache uses (`backend/prahari/assistant.py`).
2. **Retrieve:** your question is embedded, and the 4 most similar chunks are found. Chunks below
   0.58 similarity are dropped as irrelevant.
3. **Live facts:** if the question is about *now* ("Is the primary healthy?", "What's the hit rate
   right now?"), current health, metrics and recent requests are added.
4. **Plain-sentence settings:** key numbers (breaker 3 errors / 30 s, hedging rule, cache threshold,
   what happens when Redis is down…) are also given as plain sentences built from the live config,
   because a 3B model misreads tables.
5. **Generate through the gateway itself.** The question goes to `/v1/chat/completions` like any
   other client, with its own key (`prahari-assistant-key`). The chatbot is a live demo of the product:
   it gets cached, traced and routed like everything else.
6. **Show the evidence:**
   - citations [1], [2] point to the source documents;
   - a **trace chip** shows the cache result, backend, tokens, time and estimated savings;
   - "watch this answer's journey" replays it on the Architecture map.
7. **Refuse to invent:** if nothing relevant is found, it says *"The project docs don't cover that,
   so I won't guess."*

**Honest limit:** it's a 3B model, so an unusually worded question can still get a wrong number. It
was tested with 12 factual questions after the fixes above, and all 12 were correct.

---

## 8. The tech stack, and why each piece

### Backend (Python 3.12, managed with `uv`)

| Tech | What it is | Why it's used here |
| --- | --- | --- |
| **FastAPI** | Async Python web framework | Streams thousands of SSE chunks without blocking; auto-generates the OpenAPI schema. |
| **uvicorn** | The ASGI server that runs FastAPI | Production-grade async server. |
| **asyncio** | Python's concurrency model | One process handles many requests at once by switching while waiting on the network. |
| **httpx** | Async HTTP client | Streams from model servers; it's the raw core path, so failover and caching stay fully visible. |
| **Pydantic / pydantic-settings** | Data validation | Validates the YAML config and API bodies; settings come from env vars. |
| **redis-py** | Redis client | Caches, vector search, Lua rate limits. |
| **fastembed (bge-small)** | Fast CPU embeddings | 384-d vectors in-process, no GPU needed. |
| **NumPy** | Math on arrays | Similarity search over the chatbot's doc chunks. |
| **Presidio + spaCy** | PII detection | Finds names, emails, phones; adapted from Veritas. |
| **OpenTelemetry SDK** | Tracing standard | One span per stage, exported to Jaeger over OTLP/HTTP. |
| **SQLite** | Embedded database | The usage ledger, with no server to run. |
| **LiteLLM** (optional) | Multi-provider adapter | Demonstrated as one backend; not in the core path, by design. |
| **pytest, ruff, mypy** | Tests, lint, types | Quality gates. |

> **Why not route everything through LiteLLM?** LiteLLM has its own retries and fallbacks. If it
> owned failover, the gateway's own breakers, hedging and caching would be hidden inside a library,
> and the whole point of the demo is to *see* them. So the core uses raw httpx, and LiteLLM is
> just one backend. (docs/DECISIONS.md)

### Frontend (TypeScript)

| Tech | What it is | Why it's used here |
| --- | --- | --- |
| **React 19** | UI library | Components and state; the industry standard. |
| **Vite** | Dev server and bundler | Instant reloads; splits code so each page loads only what it needs. |
| **TypeScript** | JavaScript with types | Catches mistakes before running (`tsc`). |
| **Tailwind CSS v4** | Utility-first CSS | Fast styling; all colours come from tokens in `styles/tokens.css`. |
| **Motion** (`motion/react`) | Animation library | Springs, page transitions, layout animations. |
| **three.js + React Three Fiber + drei** | 3D in the browser | The neuron dive, the galaxy and the robot. R3F lets you write 3D scenes as React components. |
| **postprocessing (Bloom)** | Screen effects for three.js | The soft glow on bright objects. |
| **@xyflow/react (React Flow)** | Node-graph library | The detailed architecture map. |
| **ECharts** | Charting library | Dashboard charts (tree-shaken, loaded only when a chart appears). |
| **zustand** | Tiny state store | Holds live data (`lib/live.ts`) shared by every page. |
| **React Router** | Page routing | URLs like `/architecture?view=detailed`. |
| **react-markdown** | Markdown renderer | The Docs page. |
| **Vitest, Playwright** | Unit and browser tests | Unit tests, real-browser end-to-end tests, screenshots, frame-time profiling. |
| **ESLint** | Linter | Includes the rule that bans `Math.random`. |

### Infrastructure

| Tech | Why |
| --- | --- |
| **Docker via Colima** | Runs Redis Stack and Jaeger as containers on the Mac (`infra/docker-compose.yml`). |
| **make** | One-word commands: `make setup`, `make dev`, `make test`, `make e2e`, `make sweep`, `make perf`, `make memory`. |
| **Apple Vision** (`scripts/cutout.swift`) | Cut the robot pictures out of their backgrounds on-device. |
| **AVIF / WebP** | Modern image formats, many times smaller than PNG. |

---

## 9. How the frontend is built

### The live data layer (`frontend/src/lib/live.ts`)

The most important frontend file. It:
1. opens the WebSocket to `/ws/events` and polls `/api/architecture`, `/api/health` and
   `/api/metrics`;
2. stores everything in a **zustand** store, so any component can read it;
3. lets components subscribe to raw events with `onEvent()`, which is how packets and comets move;
4. batches incoming events and flushes them every 250 ms, so React doesn't re-render on every event;
5. switches to **replay mode** if the gateway can't be reached, playing
   `public/replay/fixture.json`: 686 real events recorded from this machine
   (`scripts/record_fixture.py`), with their original timing.

### Two ways to animate

- **Declarative (React + Motion):** "this panel should be at opacity 1". Motion animates the change.
  Good for UI.
- **Imperative (outside React):** packets on the map are SVG circles created and moved directly with
  `requestAnimationFrame` (`components/arch/packets.ts`, `PulseEdge.tsx`). Re-rendering React 60 times
  a second for every packet would be far too slow.

### Design tokens

Every colour, glow, radius and duration is a CSS variable in `styles/tokens.css` (for example
`--cyan`, `--violet`, `--space-0`). Components use only tokens, never raw hex values, so the theme
can be changed in one place. WebGL can't read CSS variables, so the 3D scenes restate the few colours
they need, with a comment pointing back to the tokens.

**The colour language:**
- **cyan:** healthy or live;
- **violet:** cached;
- **amber:** degraded, hedged or failed over;
- **red:** broken or rejected.

**Glow is earned:** only live or important things glow.

### Accessibility, built in

- Reduced motion and the Pause motion control: every animation has a still version that keeps the
  information.
- Full keyboard navigation and visible focus rings.
- One `h1` per page, alt text on every image, labelled buttons, and "Show as table" on every chart.
- Lighthouse accessibility scores 100 on every page.

### Code splitting (why pages load fast)

Each page is `lazy(() => import("./pages/X"))`, and heavy libraries (three.js, ECharts, React Flow,
markdown) load only on the pages that use them. After your first interaction, the other pages are
preloaded in the background, so switching tabs takes about 0.2 s.

---

## 10. Testing: how we know it works

| Layer | What | Count |
| --- | --- | --- |
| Backend unit tests | Every stage on its own: keys, buckets, breakers, PII, config | part of 61 |
| Backend HTTP tests | The real gateway app against **fake model servers** (`tests/fake_backend.py`) that can be made slow, broken or wrong on purpose | part of 61 |
| SIGSTOP integration test | Really freezes a backend process and checks the breaker opens and traffic fails over | 1 |
| Frontend unit tests | Formatting, the `Math.random` guard | 7 |
| End-to-end (Playwright) | A real browser against the real running system | 16 |
| Page sweep (`make sweep`) | Every page at 1440, 1280, 768 and 390 px: console errors, failed requests, overflow, heading count, alt text | 40 loads |
| Lighthouse | Performance, accessibility, best practices | 3 pages × 2 devices |
| Frame times (`make perf`) | Real fps on the Mac GPU | 6 scenarios |

Some of the end-to-end tests worth reading (`frontend/e2e/prahari.spec.ts`):
- "killing the primary turns its breaker red on the graph within 2 s";
- "ten identical requests make one upstream call";
- "the chatbot answers a docs question with a citation and a trace chip";
- "offline: the site switches to labelled replay mode".

> **Learning note: fakes vs mocks.** The backend tests don't "mock" httpx. They start small real HTTP
> servers that behave like model servers. That tests the actual network code, timeouts and streaming,
> which is where gateway bugs live.

---

## 11. Performance lessons learned the hard way

Each of these was measured, not guessed:

1. **Measure frames, don't assume them.** `tools/fps.mjs` records every frame's duration in a real
   browser on the Mac GPU.
2. **Animate only transform and opacity.** Animating `box-shadow`, `filter` or CSS custom properties
   forces the browser to repaint every frame. Transform and opacity run on the GPU compositor.
3. **Don't call expensive browser APIs every frame.** Packet positions came from
   `getPointAtLength()` every frame; sampling each path once and interpolating cut that from 310 ms
   to 8 ms per burst.
4. **Reading computed CSS forces a style recalculation.** The charts re-read colour tokens on every
   update; caching them removed 128 ms of work every 5 s.
5. **Bundle splitting can backfire.** Hand-written "manual chunk" rules accidentally pulled three.js
   (280 KB) into the home page. Removing them took mobile Lighthouse on Home from 69 to 91.
6. **Defer what the first screen doesn't need.** The 3D robot and the page preloading wait for the
   first interaction, so the first paint is fast.
7. **Profile before you "fix".** An A/B test showed a suspected culprit (spinning borders) wasn't the
   real bottleneck. Profiling showed where the time actually went.
8. **The machine matters.** Frame rates varied ±20 fps between runs because other apps were using CPU.
   Report ranges honestly.

---

## 12. Real bugs found, and what each teaches

| Bug | Lesson |
| --- | --- |
| redis-py 8 returned search results as a dict, not a list, so every semantic lookup missed | Library upgrades change data shapes. Test the real integration, not just your code. |
| The duplicate storm reported "10 upstream calls" because the semantic cache answered near-identical prompts | Features interact. A test that passed once can fail on the second run, so test repeated runs. |
| "Send 10× at once" from the browser coalesced only 8 | Browsers allow only 6 connections per host. Know your platform's limits. |
| Clicking a node often did nothing (always on phones) | A re-render between mouse-down and mouse-up sends the click to a parent. Taps hit this every time. |
| `/docs` on the production build showed the API's Swagger page | Two systems claimed the same URL. Test the production build, not just dev. |
| The privacy filter hid "Redis" as a person's name | ML-based detectors make category mistakes. Tune them to your domain and test both directions. |
| The chatbot said a breaker "stays open for 3 consecutive errors" | Small models misread tables. Give them facts as plain sentences. |
| Tab switches flashed blank, and headlines appeared last | Where you put a loading boundary decides what users see while code loads. Gradient text can't paint animating children. |
| Labels vanished in the 3D galaxy | A library (drei `Html`) re-parented its React root at the wrong moment. Sometimes the simplest fix is to drop the abstraction. |

---

## 13. How to read the code

### Backend, in this order

1. `backend/config/gateway.yaml`: the whole system's shape in one file (backends, models, keys, limits).
2. `prahari/main.py`: how the app starts and wires everything together.
3. `prahari/api/gateway.py`: the OpenAI-compatible endpoint.
4. `prahari/pipeline.py`: **the request journey** (section 3). The most important file.
5. `prahari/cache.py`, `prahari/coalesce.py`, `prahari/ratelimit.py`, `prahari/budget.py`: the stages.
6. `prahari/router.py` and `prahari/breaker.py`: routing, hedging, failover (section 4).
7. `prahari/events.py`, `prahari/metrics.py`, `prahari/telemetry.py`: visibility (section 5).
8. `prahari/chaos.py` and `prahari/scenarios.py`: breaking things on purpose.
9. `prahari/assistant.py`: the chatbot (section 7).
10. `tests/test_gateway.py`: every behaviour, demonstrated as a test.

### Frontend, in this order

1. `src/main.tsx`: routes and lazy pages.
2. `src/lib/live.ts`: the live data layer.
3. `src/components/Shell.tsx`: the frame every page lives in.
4. `src/styles/tokens.css`: the design system.
5. `src/pages/Playground.tsx`: the simplest page that sends real requests.
6. `src/pages/Architecture.tsx` with `components/arch/`: the flagship view.
7. `src/scenes/`: the 3D scenes (Brain, Galaxy3D, Robot3D).
8. `e2e/prahari.spec.ts`: what the whole site promises, as tests.

---

## 14. Exercises

Do these with `make dev` running. Keep the Architecture page open in another window.

1. **Watch a cache hit.** In the Playground, send a question. Send it again. Compare the times and the
   `cache:` value. Then tick "Bypass cache" and send again.
2. **Find the semantic threshold.** Use "Rephrase and resend" several times. Which rewordings still hit?
   Look at the similarity number in the trace chip.
3. **Trip a breaker by hand.** In the Chaos Lab, kill MLX. Send three Playground requests. Watch the
   breaker open on the map and in the live path graph, then revert and watch it heal.
4. **See hedging.** Set MLX latency to 4,000 ms and send a request. Which backend answered? Open the
   trace and find the hedge.
5. **Read a trace.** In Traces, open any request. Which stage took longest? Why?
6. **Change the system without touching the frontend.** In `gateway.yaml`, change `open_seconds` from
   30 to 10 and save. The gateway re-reads the file when it changes, with no restart. Click the MLX
   node on the Detailed map: its live config now says 10. (The chatbot's document index is built at
   startup, so it may quote the old value until the gateway restarts. Why might that be a reasonable
   trade-off?) Set it back to 30 afterwards.
7. **Add a backend.** Read the commented `vllm-gpu` entry in the YAML. What would you change to add a
   second Ollama model? Where would it appear on the Architecture page?
8. **Break the privacy filter.** In the Playground, try prompts with names, emails and phone numbers,
   and check what the trace says was redacted. Can you find a name it misses?
9. **Run the tests.** `make test` and `make e2e`. Open `tests/test_gateway.py` and pick one test to
   read slowly.
10. **Go offline.** Stop the gateway (Ctrl+C on `make dev`, or stop uvicorn) and reload the site.
    What changes? What stays the same?

---

## 15. Glossary

| Term | Meaning |
| --- | --- |
| **Gateway** | A single entry point that sits between clients and backend services and applies shared policies. |
| **Tenant** | One customer, team or app sharing the gateway, with its own key, limits and budget. |
| **Token** | A chunk of text (about ¾ of a word) that models read and write; the unit of cost and rate limits. |
| **TPM** | Tokens per minute: the rate-limit unit. |
| **SSE** | Server-sent events: a one-way stream of text events over HTTP, used for token-by-token answers. |
| **WebSocket** | A two-way connection that stays open; used to push live events to the site. |
| **Embedding** | A list of numbers representing the meaning of a text (here, 384 numbers). |
| **Cosine similarity** | How closely two embeddings point in the same direction (1 = same meaning). |
| **HNSW** | A graph index for fast nearest-neighbour search over embeddings. |
| **Semantic cache** | A cache that matches by meaning, not exact text. |
| **Coalescing / singleflight** | Merging identical in-flight requests into one upstream call. |
| **Slot** | One concurrent request a backend can handle. |
| **Circuit breaker** | A switch that stops sending traffic to a failing backend, then probes it to see if it has recovered. |
| **Half-open** | The breaker state where exactly one probe request is allowed through. |
| **Failover** | Retrying on another backend after a failure. |
| **Hedging** | Sending a duplicate request to a second backend when the first is slow; the first to answer wins. |
| **TTFT** | Time to first token: how long until the answer starts streaming. |
| **p50 / p95** | The latency that 50% / 95% of requests beat. p95 shows the slow tail. |
| **Span / trace** | A span is one timed step; a trace is all the spans of one request. |
| **OpenTelemetry (OTel)** | The open standard for traces, metrics and logs. |
| **OTLP** | The OpenTelemetry protocol for sending traces to a backend like Jaeger. |
| **PII** | Personally identifiable information: names, emails, phone numbers, IDs. |
| **Prompt injection** | Text that tries to override a model's instructions. |
| **RAG** | Retrieval-augmented generation: retrieve relevant text, then generate from it. |
| **SIGSTOP** | A Unix signal that freezes a process without killing it. |
| **Chaos engineering** | Deliberately injecting failures to prove a system copes. |
| **Lua script (Redis)** | Code Redis runs atomically, so read-modify-write can't race. |
| **Replay mode** | The site playing recorded real traffic when the gateway is offline, clearly labelled. |
| **LCP / TBT / CLS** | Lighthouse metrics: when the main content appears, how long the page is blocked, how much it jumps. |
| **Reduced motion** | An OS accessibility setting asking sites to minimise animation. |
| **Design tokens** | Named design values (colours, sizes, durations) defined once and reused everywhere. |

---

**Where to go next:**
- `docs/ARCHITECTURE.md`: the precise technical reference.
- `docs/DECISIONS.md`: why each choice was made, and what was rejected.
- `docs/DEMO_SCRIPT.md`: an 8-minute walkthrough to present the project.
- `docs/VALIDATION_REPORT.md`: every requirement with its honest pass or fail.


---

## Bonus chapter: from demo to production (added 2026-11-05)

The first version worked; this pass made it safe to run for other people. Read these in order:

1. **Secure by default** (`backend/prahari/security.py`). The gateway starts in production mode
   unless told otherwise. A key is checked in constant time and only its public id travels any
   further. Tenants can only read their own requests, enforced in the SQL itself.
2. **Bounded input** (`pipeline._check_limits`, `api/gateway._read_body`). Before any expensive
   work, the body size, message count and token counts are checked, with a clear error for each.
3. **Two more guardrails.** Llama Guard 3 judges whether a prompt (and later the answer) is
   harmful, but only blocks when it is confident (`guardrails/safety.py`). The output guard hides
   emails, phone numbers, card numbers and secrets the model writes, even when a value arrives
   split across two chunks (`guardrails/output.py`).
4. **Real serving engines.** vLLM and the LiteLLM proxy are real servers behind the same YAML
   chains; nothing in the gateway changed to add them.
5. **Seeing everything.** `/metrics` for Prometheus, JSON logs with the trace id in every line,
   and the same traces in Langfuse when it is running.
6. **Proving it.** `backend/evals/` scores the gateway's own judgements against labelled examples;
   CI fails if any of them gets worse. `docs/PRODUCTION_READINESS.md` collects the evidence.
