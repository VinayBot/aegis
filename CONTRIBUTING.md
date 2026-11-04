# Contributing

1. `make setup` once; `make infra` for Redis and Jaeger; `make dev` for the whole system.
2. Before a commit: `make lint typecheck test evals`. With `make dev` running: `make e2e sweep`.
3. A change to a guardrail, the cache threshold or the safety threshold comes with a row in
   `backend/evals/data/` that shows it, and the eval gate must still pass.
4. A new backend is a YAML entry (`backend/config/gateway.yaml`); it appears on the map and in
   the Chaos Lab with no frontend change.
5. Commits: conventional prefixes (`feat:`, `fix:`, `docs:`), one idea per commit, the why in the
   body. Decisions that a reader could question go in docs/DECISIONS.md.
