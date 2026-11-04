# Peer audit

A guide for a classmate auditing AEGIS, and the record of the audits done so far. Each check has
the command or the place to look, so an audit takes about 30 minutes and needs no prior context. 24 findings are recorded and fixed below.

## How to audit (for the reviewer)

Run `make setup` once, then `make dev` in one terminal. Every check below either passes or is a
finding. Please add your findings to the table at the end (or open an issue).

### 1. It does what the brief says

| Check | How |
| --- | --- |
| OpenAI-compatible SSE streaming | `./scripts/demo_stream.sh` prints `content-type: text/event-stream` and the answer token by token |
| Semantic cache | Playground: Send, then "Rephrase and resend": `cache: semantic` with the similarity |
| Model failover | Chaos Lab, "Primary crashes": every request is still answered; the breaker opens and heals |
| Local tracing | Traces tab, or Jaeger at :16686: one trace per request, one span per stage |
| WebSockets | Playground, Transport: WebSocket, then Stop mid-answer |

### 2. It is secure

| Check | How |
| --- | --- |
| Secure by default | `cd backend && uv run uvicorn aegis.main:app --port 8010`, then `curl -i localhost:8010/api/config`: expect 401 (production mode is the default) |
| No raw keys leak | `curl -s localhost:8000/api/tenants localhost:8000/api/config \| grep -c aegis-demo-key` prints 0 |
| No IDOR | `test_a_tenant_never_sees_another_tenants_requests` in `backend/tests/test_production.py` |
| Input bounds | `curl -s localhost:8000/v1/chat/completions -H 'authorization: Bearer aegis-demo-key' -H 'content-type: application/json' -d '{"messages":[{"role":"wizard","content":"x"}]}'` is a 400 |
| PII never reaches a model | Playground: "my name is vinay and vinaysharma@gmail is my email": the model sees placeholders |
| Unsafe prompts blocked | Playground: a weapons question returns `HTTP 400 · unsafe_content` |

### 3. It is reliable and observable

| Check | How |
| --- | --- |
| Tests | `make test` (backend 148, frontend 8), `make e2e` (21) |
| Evaluation gate | `make evals-live`: five suites, all PASS |
| Probes and metrics | `curl localhost:8000/readyz`, `curl localhost:8000/metrics \| head` |
| Clean shutdown | Ctrl-C `make dev` with the site open: the gateway exits within 5 s |
| Production image | `make image`: builds, refuses anonymous admin calls, runs as uid 10001 |

### 4. The code is reviewable

| Check | How |
| --- | --- |
| Lint and types | `make lint`, `make typecheck` |
| Where each Week 5 topic lives | README, "Week 5 coverage" |
| Why each choice was made | docs/DECISIONS.md |

## Audit record

### Self-audit, 2026-11-04 (author, full demo rehearsal)

| # | Finding | Severity | Fix |
| --- | --- | --- | --- |
| 1 | The PII filter redacted the project's own words ("Mac", "Redis Stack", "Playground"), so the assistant answered "AEGIS runs on a single <PERSON>" | medium | Known non-names list; a real name beside them is still redacted |
| 2 | "my name is vinay and vinaysharma@gmail is my email" reached the model unredacted | high | Self-introduced names in any case, emails without a TLD |
| 3 | The assistant answered "[1]" to "What just happened to the primary?" | medium | A plain timeline of recent events first in the live context; the demo script no longer indexed |
| 4 | The crash scenario could say "Probe served by ollama-llama" | low | It reports only what is certain |

### Self-audit, 2026-11-05 (author, against the Week 2-4 report cards and the capstone checklist)

| # | Finding | Severity | Fix |
| --- | --- | --- | --- |
| 5 | Control plane unauthenticated; `/api/config` returned every key and backend credential; `/api/tenants` returned raw keys | high | Secure-by-default production mode, admin role, public key ids, masked config |
| 6 | Any caller could read any request by id | high | Tenant-scoped SQL; 404 across tenants |
| 7 | No bound on body size, message count, prompt or output tokens | medium | Streamed body limit (413) and explicit 400 codes |
| 8 | An idle `/ws/events` client kept a stopping server alive for ever | medium | The handler watches for the close; regression test |
| 9 | Raw keys in the ledger, Redis key names and events | medium | Key ids everywhere; old ledgers migrated |
| 10 | Five backends drew on top of the relay and embedder on the map | low | Backends on their own row |
| 11 | Llama Guard 1B blocked "How do I kill a Python process?" | medium | Block only at P(unsafe) >= 0.9 (measured separation) |
| 12 | The eval found three injection framings the regex missed | medium | Patterns added; held-out set added |
| 13 | The docs index embedded in one 256-item batch: a 1.49 GB peak, fatal in a 2 GB container | high | Batches of 16 (444 MB peak) |
| 14 | Tool calls were dropped by the gateway (Week 2 gap: no native tool calling) | medium | `tools`, `tool_choice`, `response_format` pass through, stream, assemble and key the cache |

### Independent review, 2026-11-05 (a separate reviewing agent, high effort, every finding reproduced)

| # | Finding | Severity | Fix |
| --- | --- | --- | --- |
| 15 | The output guard's number hold-back regex had nested quantifiers: "(1) (1) ... x" backtracked exponentially (25 s for 14 words), freezing the event loop for every tenant | critical | A linear backwards scan; regression test (60 words in under 0.2 s) |
| 16 | A run with no whitespace was released at an arbitrary character, so a secret straddling the cut escaped | high | Cut only at characters a value cannot contain (quotes, commas, braces...); minified-JSON test at three chunk sizes |
| 17 | `role: ["user"]` raised TypeError: a 500, and no request record | medium | Type-checked roles (400); any unexpected error is a recorded, traced 500 |
| 18 | A WebSocket cancel before the first token skipped the bookkeeping, and an unstarted stream could leak its coalescing flight | high | Cancellation is recorded (499) and settled; `Prepared.abandon` frees the slot and the flight; test |
| 19 | The production image accepted the repo's public demo keys | high | Production ignores plaintext keys (only `sha256:`); CI checks the demo key is refused |
| 20 | Llama Guard never saw system, developer or tool messages | high | They are folded into the classified turns; test |
| 21 | `max_completion_tokens` was neither capped nor forwarded; a backend without a default had no cap | medium | Treated as `max_tokens`; every request carries a cap |
| 22 | A YAML typo made `/readyz` fail on every replica although the last good config kept serving | medium | Reported, not failed; test |
| 23 | The Playground's WebSocket promise never settled if the server closed without a final frame | low | `onclose` settles it |
| 24 | In dev, any website open in the same browser could drive the loopback-trusted control plane | medium | A foreign `Origin` is never the local operator; test |

### Peer audit (to be filled in by the reviewer)

| # | Reviewer | Date | Finding | Severity | Status |
| --- | --- | --- | --- | --- | --- |
| | | | | | |
