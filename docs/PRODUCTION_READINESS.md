# Production readiness assessment

The Day 25 capstone checklist, item by item, with the evidence for each and the honest gaps.
Measured on 2026-11-05 on the build machine (Apple Silicon, 16 GB), on the real system unless a
row says otherwise.

**Verdict: ready for a single-node deployment in front of local or cloud models.** Every
checklist item is met with a test or a measurement behind it. Scaling past one node needs the
three changes listed under "Known gaps".

## The capstone checklist

| Item | Status | Evidence |
| --- | --- | --- |
| Tenant context and RBAC (API keys or JWT) | met | API keys (hashed in production) or short-lived HS256 JWTs exchanged at `POST /v1/auth/token`, carrying tenant and role; a token never outranks its key and dies with it. Tests: `tests/test_jwt.py`. |
| Multi-tenant isolation; zero IDOR | met | Every tenant-visible read filters by tenant in SQL (`ledger.RECENT_TENANT_SQL`, `GET_TENANT_SQL`); another tenant's request id is a 404. Tests: `test_a_tenant_never_sees_another_tenants_requests`, `test_access_rules`. Caches are partitioned by tenant (the tenant is in every cache key). |
| Security and guardrails: Presidio PII, Llama Guard safety | met | Presidio + regex redaction before any cache, embedder or model; Llama Guard 3 on input (cache misses) and output (before caching); streaming output redaction. Evals: PII recall 0.938 / 0 false positives, Llama Guard recall 1.0 / 0 false positives, output redaction 1.0 / 0. |
| Automated evals gate in CI | met | `backend/evals/`: five golden sets with thresholds, run in the test suite (`test_every_offline_eval_gate_passes`) and as a CI artifact. Results below. |
| Resilience: explicit timeouts, backoff with jitter, multi-provider fallback | met | httpx timeouts on every call (connect 2 s, read 60 s; probes 0.4 s; Llama Guard 2.5 s); first-token timeout 15 s + 1 s/1k tokens; chain retry with full-jitter backoff; fallback chains across MLX, Ollama, vLLM and the LiteLLM proxy; hedging; circuit breakers on consecutive errors and on a 50% failure rate over 30 s, healing without traffic. |
| Performance: semantic cache, SSE streaming, query plans | met | Exact and semantic caches in Redis Stack (HNSW); SSE and WebSocket streaming from async generators; `EXPLAIN QUERY PLAN` checked in tests (`test_tenant_queries_use_an_index`). Load test below. |
| Hardened deployment: multi-stage non-root image, probes, JSON logs | met | `Dockerfile`: three stages, pinned bases, uid 10001, HEALTHCHECK; compose adds a read-only root, no capabilities, no-new-privileges. `/healthz` (liveness) and `/readyz` (readiness: 503 when no backend can serve). JSON logs with request id, trace id and tenant. CI builds the image and checks all of this. |
| Telemetry: Langfuse tracing + Prometheus /metrics | met | OTLP to Jaeger always and to Langfuse with keys; verified on self-hosted Langfuse 4.67.0 (docs/evidence/langfuse-*.png). User ratings (`POST /v1/feedback`) become Langfuse scores on the trace and `aegis_feedback_total`. `GET /metrics` in Prometheus format. |

## Evaluation gate

`make evals` (offline) and `make evals-live` (plus Llama Guard). Report: `backend/evals/report.json`.

| Suite | n | Gate | Result |
| --- | --- | --- | --- |
| Semantic cache (bge-small, threshold 0.95) | 30 pairs | precision 1.0, recall >= 0.6 | precision 1.0, recall 1.0 |
| PII redaction (input) | 28 | recall >= 0.9, FP rate <= 0.1 | recall 0.938, FP 0.0 |
| Prompt injection | 31 (7 held out) | recall >= 0.85, FP rate <= 0.1 | recall 0.944, FP 0.0 |
| Output redaction | 16 | recall 1.0, FP 0.0 | 1.0, 0.0 |
| Llama Guard 3 1B at P(unsafe) >= 0.9 | 18 | recall >= 0.85, FP rate <= 0.1 | 1.0, 0.0 |
| AEGIS assistant, a RAG system over these docs (live) | 13 | retrieval >= 0.9, answer accuracy >= 0.9, abstention 1.0 | 1.0, 1.0, 1.0 |

The cache threshold sits in a measured gap: the least similar true paraphrase scores 0.967, the
most similar different question ("orders from last week" vs "last month") 0.940. The two misses
are honest: a name spaCy's small model does not know ("Ananya Iyer") and one held-out injection
phrasing ("forget everything you were told before").

## Tests

| Suite | Result |
| --- | --- |
| Backend: unit, HTTP integration against fake model servers and real Redis, SIGSTOP, eval gate | 160 passed, 91% line coverage (CI floor 85%) |
| Frontend unit (vitest) | 7 passed |
| End to end on the real system (Playwright) | 21 passed |
| Page sweep: 10 routes x 4 widths (console errors, failed requests, overflow, a11y basics) | 40 of 40 clean |
| Production image smoke test | alive, not ready without models (503), anonymous admin call 401, uid 10001, JSON logs |

## Load test

`make loadtest`, 12 concurrent clients, streaming, real models (docs/evidence/loadtest.json).

| Phase | Requests | Throughput | p50 | p95 | Errors |
| --- | --- | --- | --- | --- | --- |
| Gateway overhead (every request an exact cache hit) | 60 | 233 req/s | 48 ms | 77 ms | 0 |
| Mixed workload (repeats and paraphrases) | 120 | 18.4 req/s | 23 ms | 2.5 s | 0 |

In the mixed run 102 of 120 requests were answered without a model call (56 exact hits, 21
semantic hits, 25 coalesced). The p95 is the local 3B model on a laptop under load; the
gateway's own share is the overhead row. Before the MLX primary was given 4 slots (see below)
the same run gave 8.5 req/s with a 6.8 s p95.

## Serving benchmark

`make bench-serving` (docs/evidence/serving_bench.json): the same 96-token generation sent
straight to each server at 1, 2, 4 and 8 at once.

| Server | 1 | 2 | 4 | 8 | Latency 1 to 8 |
| --- | --- | --- | --- | --- | --- |
| vLLM (Metal), Qwen2.5-0.5B | 249 tok/s | 458 | 844 | 1,036 (x4.2) | 0.39 to 0.74 s |
| mlx_lm.server, Qwen2.5-3B | 61 tok/s | 119 | 212 | 232 (x3.8) | 1.58 to 3.04 s |

vLLM's continuous batching keeps scaling to 8 with almost flat latency. mlx-lm 0.32 also
batches, but on this GPU its throughput stops growing past 4 while latency doubles, so its
backend has 4 slots (it had 1, from an older mlx-lm that served one request at a time).

## Service levels (targets for this deployment)

| SLI | Target | How it is measured |
| --- | --- | --- |
| Availability of the data plane | 99.5% of requests not 5xx, per day | `aegis_requests_total` by status |
| Cache-hit latency | p95 under 300 ms | `aegis_request_duration_seconds{cache="exact"}` |
| Time to first token on a miss | p95 under 3 s | `aegis_time_to_first_token_seconds` |
| Breaker recovery | a healed backend closes within open_seconds + 1 probe | `aegis_breaker_transitions_total` |

## Runbook

| Symptom | Check | Action |
| --- | --- | --- |
| `/readyz` 503 | `/api/health`: which backends are down or open | Start or revive the model server; the breaker probes and closes by itself |
| 503 `no_backend_available` bursts | `aegis_breaker_state`, slot gauges | All breakers open: fix the backends; all busy: add slots or a backend to the chain |
| Many 429s | `aegis_rejections_total{reason="rate_limit"}` per tenant | Raise that key's `tpm` in the YAML (hot-reloaded) |
| `safety.skipped` events | Ollama and `llama-guard3:1b` loaded? | `ollama pull llama-guard3:1b`; for strict tenants set `on_error: block` |
| Cache hit rate falls | `aegis_requests_total{cache=...}` | Redis healthy? `redis_ok` in `/api/health`; the semantic threshold in `/api/config` |
| Wrong answers from the cache | the semantic eval and near-miss similarities | Raise `cache.semantic_threshold` (`POST /api/cache/tune`), flush, add the pair to `evals/data` |

## Known gaps

- **One node.** Coalescing, the event bus and live metrics are in-process, and the ledger is
  SQLite (WAL, batched writes). Several replicas would need Redis-backed singleflight and pub/sub
  and a shared database (Postgres); the code isolates each behind one module.
- **Langfuse is optional, not always on.** It needs 3-4 GB; Jaeger is the always-on viewer.
- **Injection detection is a regex layer.** It catches the common framings (eval above) but not
  every paraphrase; the DeBERTa classifier did not fit the memory budget (DECISIONS.md).
- **API keys, not SSO.** Admin access is a key; a deployment with many operators would add an
  identity provider in front of the control plane.
- **The container reaches host models only if they listen beyond loopback** (`OLLAMA_HOST=0.0.0.0`,
  `--host 0.0.0.0`); the image itself is verified without models.
