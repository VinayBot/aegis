# Security

## Reporting

Please report a vulnerability privately through GitHub's "Report a vulnerability" (Security tab)
rather than an issue. Expect a reply within a week.

## What AEGIS protects, and how

| Threat | Control | Where |
| --- | --- | --- |
| Unauthenticated control of the gateway | Production mode by default: admin credential required for every state-changing or cross-tenant route; chaos refused | `aegis/security.py`, `aegis/main.py` |
| Key theft from config, logs or APIs | `sha256:` key entries (production ignores plaintext ones), constant-time lookup, public key ids everywhere else, masked backend credentials, backend keys from the environment | `aegis/security.py`, `aegis/config.py` |
| Cross-site requests to a dev gateway | Loopback is the operator only without a foreign `Origin` | `aegis/security.py` |
| Cross-tenant data access (IDOR) | Tenant filter in SQL for every tenant-visible read; tenant in every cache key | `aegis/ledger.py`, `aegis/cache.py` |
| Resource exhaustion | Streamed body limit, message, prompt and output token bounds; per-key token bucket; per-tenant budget; slot limits and queue deadline per backend | `aegis/api/gateway.py`, `aegis/pipeline.py`, `aegis/ratelimit.py` |
| Personal data reaching a model or a trace | Presidio + regex redaction before any cache, embedder or model; traces and logs carry counts only | `aegis/guardrails/pii.py` |
| Personal data or secrets in model output | Streaming output redaction | `aegis/guardrails/output.py` |
| Prompt injection | Regex framings (evaluated, see docs/PRODUCTION_READINESS.md) | `aegis/guardrails/injection.py` |
| Harmful content | Llama Guard 3 on input and output; unsafe answers are never cached | `aegis/guardrails/safety.py` |
| Supply chain | Pinned images and serving wheels; LiteLLM held below the compromised 1.82.7/1.82.8; pip-audit, npm audit and CodeQL in CI; Dependabot | `Makefile`, `.github/` |
| Container escape impact | Non-root uid 10001, read-only root, all capabilities dropped, no-new-privileges | `Dockerfile`, `infra/docker-compose.yml` |

## Known limits

Admin access is an API key, not SSO. The injection check is a regex layer, not a classifier. Dev
mode (`AEGIS_ENV=dev`) trusts loopback callers and enables chaos: never run it on a shared host.
The demo keys in `backend/config/gateway.yaml` are public on purpose; replace them with `sha256:`
entries (`uv run python -m aegis.security hash <key>`) anywhere else.
