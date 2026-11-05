# Security

## Reporting

Please report a vulnerability privately through GitHub's "Report a vulnerability" (Security tab)
rather than an issue. Expect a reply within a week.

## What PRAHARI protects, and how

| Threat | Control | Where |
| --- | --- | --- |
| Unauthenticated control of the gateway | Production mode by default: admin credential required for every state-changing or cross-tenant route; chaos refused | `prahari/security.py`, `prahari/main.py` |
| Key theft from config, logs or APIs | `sha256:` key entries (production ignores plaintext ones), constant-time lookup, public key ids everywhere else, masked backend credentials, backend keys from the environment | `prahari/security.py`, `prahari/config.py` |
| Key guessing | 20 failures per address per minute, then 429 + Retry-After on every entry point | `prahari/security.py` |
| Cross-site requests to a dev gateway | Loopback is the operator only without a foreign `Origin` | `prahari/security.py` |
| Cross-tenant data access (IDOR) | Tenant filter in SQL for every tenant-visible read; tenant in every cache key | `prahari/ledger.py`, `prahari/cache.py` |
| Resource exhaustion | Streamed body limit, message, prompt and output token bounds; per-key token bucket; per-tenant budget; slot limits and queue deadline per backend | `prahari/api/gateway.py`, `prahari/pipeline.py`, `prahari/ratelimit.py` |
| Personal data reaching a model or a trace | Presidio + regex redaction before any cache, embedder or model; traces and logs carry counts only | `prahari/guardrails/pii.py` |
| Personal data or secrets in model output | Streaming output redaction | `prahari/guardrails/output.py` |
| Prompt injection | Regex framings (evaluated, see docs/PRODUCTION_READINESS.md) | `prahari/guardrails/injection.py` |
| Harmful content | Llama Guard 3 on input and output; unsafe answers are never cached | `prahari/guardrails/safety.py` |
| Supply chain | Pinned images and serving wheels; LiteLLM held below the compromised 1.82.7/1.82.8; pip-audit, npm audit and CodeQL in CI; Dependabot | `Makefile`, `.github/` |
| Container escape impact | Non-root uid 10001, read-only root, all capabilities dropped, no-new-privileges | `Dockerfile`, `infra/docker-compose.yml` |

## Known limits

Admin access is an API key, not SSO. The injection check is a regex layer, not a classifier. Dev
mode (`PRAHARI_ENV=dev`) trusts loopback callers and enables chaos: never run it on a shared host.
The demo keys in `backend/config/gateway.yaml` are public on purpose; replace them with `sha256:`
entries (`uv run python -m prahari.security hash <key>`) anywhere else.
