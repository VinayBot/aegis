# Build plan

The user asked for the whole project to be built and tested without pausing for plan approval, so
this plan was executed as written and is kept as the record of how the work was organised.

## Phases

| Phase | Output | Where |
| --- | --- | --- |
| 0. Vet and install skills | DESIGN.md, docs/SKILLS_REVIEW.md | `.claude/skills/` (git-ignored) |
| 1. Gateway backend | the request path, control plane, tests | `backend/` |
| 2. Design system | tokens, type, palette sampled from the robots | `frontend/src/styles/tokens.css`, DESIGN.md |
| 3. Home scroll journey | hero, dive, proof strip, Meet AEGIS | `frontend/src/pages/Home.tsx`, `components/Dive.tsx`, `scenes/` |
| 4. Architecture | live graph, packets, replay, self-check, export | `frontend/src/pages/Architecture.tsx`, `components/arch/` |
| 5. The rest of the app | Control Room, Chaos Lab, Playground, Traces, Docs | `frontend/src/pages/` |
| 6. AEGIS assistant | grounded answers through the gateway | `backend/aegis/assistant.py`, `frontend/src/components/Assistant.tsx`, `AssistantBot.tsx`, `scenes/Robot3D.tsx` |
| 7. Motion language | tokens, sky shader, reveals, ticker | `frontend/src/components/fx/`, `styles/app.css` |
| 8. Quality pass | tests, e2e, Lighthouse, frame times, memory, audit | docs/VALIDATION_REPORT.md |

## File tree

```
assignment5/
  Makefile                 setup, dev, test, e2e, lint, typecheck, record-fixture, memory, perf, assets
  README.md  DESIGN.md
  backend/
    pyproject.toml         uv project (Python 3.12)
    config/gateway.yaml    backends, model chains, keys, cache/router/breaker tuning (hot-reloaded)
    aegis/                 the gateway (see ARCHITECTURE.md, Source map)
    tests/                 61 tests: units, HTTP end-to-end with fake backends, SIGSTOP integration
  frontend/
    src/                   React 19 + TypeScript
    e2e/                   Playwright tests against `make dev`
    public/assets/         robot images (AVIF/WebP)
    public/replay/         recorded real traffic for offline replay
    tools/                 screenshot and frame-time scripts
  infra/docker-compose.yml Redis Stack + Jaeger
  scripts/                 dev.sh, cutout.swift, build_assets.py, record_fixture.py, measure_memory.py
  images/                  the robot pictures supplied by the user
  docs/                    this folder
```

## Dependencies and why

**Backend.** fastapi + uvicorn (async HTTP and WebSocket); httpx (backend calls, streaming);
redis (cache, vector index, Lua rate limits); fastembed + numpy (bge-small on CPU, no torch);
presidio + spacy + en_core_web_sm (PII redaction, from Veritas); opentelemetry sdk + OTLP HTTP
exporter (traces); pydantic-settings + pyyaml (config); psutil (memory measurement). Optional:
litellm (one adapter). Dev: pytest, pytest-asyncio, ruff, mypy, websockets.

**Frontend.** react 19, react-router (pages), tailwindcss v4 (utilities over tokens), motion (all UI
motion), three + @react-three/fiber + drei + @react-three/postprocessing (star field, neural
interior, bloom), @xyflow/react (architecture graph), echarts (charts, tree-shaken), zustand (live
store), react-markdown + remark-gfm (docs page), lucide-react (icons on the map),
@fontsource-variable (self-hosted fonts). Dev: vite, typescript, eslint, vitest + testing-library,
playwright, lighthouse.

## What was judged wrong or over-scoped in the brief

- **Presidio as specified** would need a large spaCy model; the small one, as tuned in Veritas, is used.
- **Image to Code** assumes image generation, which is not available here; the user's pictures and
  screenshots were the references.
- **"60 fps"** on an M-series Mac with a 120 Hz display: measured, not assumed (see validation report).
- **Self-hosted Langfuse** does not fit in 16 GB beside two models; OTLP export keeps it one config
  change away.
- **Optional low-poly 3D robot:** not built; the photographic robots with live overlays carry more
  character, and the flying assistant is an original drawn robot instead.
