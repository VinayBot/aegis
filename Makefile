# Pinned serving stacks, each in its own environment so their dependency trees never meet the gateway's.
# vllm-metal v0.32.0 is two weeks old at the time of pinning; LiteLLM 1.80.0 predates the compromised
# 1.82.7/1.82.8 releases.
VLLM_WHEEL := https://github.com/vllm-project/vllm/releases/download/v0.32.0/vllm-0.32.0%2Bcpu-cp38-abi3-macosx_11_0_arm64.whl
VLLM_METAL_WHEEL := https://github.com/vllm-project/vllm-metal/releases/download/v0.32.0/vllm_metal-0.32.0-cp312-cp312-macosx_15_0_arm64.whl

.PHONY: perf sweep setup setup-serving evals evals-live loadtest image bench-serving langfuse-up langfuse-down infra dev test test-backend test-frontend e2e lint typecheck build serve record-fixture memory assets clean

setup: ## install everything (Python deps, spaCy model, Node deps, Playwright browser, models)
	cd backend && uv sync --extra litellm  # LiteLLM is the optional adapter; installed so it can be demonstrated
	cd frontend && npm install && npx playwright install chromium
	uv tool install --python 3.12 mlx-lm
	ollama pull llama3.2:3b
	ollama pull llama-guard3:1b   # content safety (input + output), ~110-150 ms per check
	$(MAKE) setup-serving

setup-serving: ## the real LiteLLM proxy and vLLM (Metal plugin), each in its own environment
	uv venv $$HOME/.venv-litellm-proxy --python 3.12 --allow-existing -q
	VIRTUAL_ENV=$$HOME/.venv-litellm-proxy uv pip install -q "litellm[proxy]==1.80.0"
	uv venv $$HOME/.venv-vllm-metal --python 3.12 --allow-existing -q
	VIRTUAL_ENV=$$HOME/.venv-vllm-metal uv pip install -q "$(VLLM_WHEEL)" "$(VLLM_METAL_WHEEL)"

infra: ## Redis Stack + Jaeger only
	docker-compose -f infra/docker-compose.yml up -d

dev: ## the whole system: infra, MLX primary, Ollama fallback, gateway, website
	./scripts/dev.sh

test: test-backend test-frontend ## unit + integration tests (needs `make infra` for Redis)

test-backend:
	cd backend && uv run pytest -q

evals: ## the evaluation gate: semantic cache, PII, injection, output redaction (offline)
	cd backend && uv run python -m evals.run

evals-live: ## the evaluation gate plus Llama Guard against the local Ollama
	cd backend && uv run python -m evals.run --live

test-frontend:
	cd frontend && npm test

e2e: ## browser tests against the running system (`make dev` in another terminal)
	cd frontend && npm run e2e

lint:
	cd backend && uv run ruff check aegis tests
	cd frontend && npm run lint

typecheck:
	cd backend && uv run mypy
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build

serve: build ## the built site served by the gateway itself on :8000
	cd backend && AEGIS_ENV=dev AEGIS_CHAOS_ENABLED=1 uv run uvicorn aegis.main:app --port 8000

record-fixture: ## record real traffic into the offline replay fixture (needs `make dev`)
	cd backend && uv run python ../scripts/record_fixture.py

memory: ## measure the real resident memory of every component
	cd backend && uv run python ../scripts/measure_memory.py

assets: ## rebuild the robot images and overlay regions from images/
	mkdir -p var/art
	cp images/*12.06.56* var/art/hover-src.png
	cp images/*1.20.15* var/art/meet-src.png
	swift scripts/cutout.swift var/art/meet-src.png var/art/meet-cut.png
	swift scripts/cutout.swift var/art/hover-src.png var/art/hover-cut.png
	swift scripts/cutout.swift var/art/standing-src.png var/art/standing-cut.png
	uv run --no-project --with "pillow>=11.3" python scripts/build_assets.py

sweep: ## every page at desktop and phone width: console errors, failed requests, overflow, a11y basics (needs `make dev`)
	cd frontend && node tools/sweep.mjs

perf: ## frame-time profile of the animated pages on the Mac GPU (needs `make dev`)
	cd frontend && node tools/fps.mjs

clean:
	rm -rf frontend/dist var/log/*.log

langfuse-up: ## self-hosted Langfuse v4 on :3100 (needs ~4 GB in Docker: colima start --memory 6)
	docker-compose -f infra/langfuse/docker-compose.yml up -d
	@echo "Langfuse: http://localhost:3100 (admin@aegis.local / aegis-local-only). Start the gateway with:"
	@echo "  AEGIS_LANGFUSE_HOST=http://localhost:3100 AEGIS_LANGFUSE_PUBLIC_KEY=pk-lf-aegis-local AEGIS_LANGFUSE_SECRET_KEY=sk-lf-aegis-local"

langfuse-down:
	docker-compose -f infra/langfuse/docker-compose.yml down

loadtest: ## throughput, latency and TTFT percentiles against `make dev` (writes docs/evidence/loadtest.json)
	cd backend && uv run python ../scripts/loadtest.py --requests 120 --concurrency 12 --out ../docs/evidence/loadtest.json

image: ## the production image (gateway + built site, non-root) and its smoke test
	docker build -t aegis-gateway:local .
	docker rm -f aegis-smoke >/dev/null 2>&1 || true
	docker run -d --name aegis-smoke -p 127.0.0.1:8002:8000 aegis-gateway:local
	for i in $$(seq 1 60); do curl -fs http://127.0.0.1:8002/healthz && break; sleep 2; done
	@test "$$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/api/architecture)" = 401 && echo " control plane refuses anonymous callers"
	@test "$$(docker exec aegis-smoke id -u)" = 10001 && echo "runs as a non-root user"
	docker rm -f aegis-smoke

bench-serving: ## vLLM vs MLX throughput at 1, 2, 4 and 8 concurrent requests (needs `make dev`)
	cd backend && uv run python ../scripts/bench_serving.py --out ../docs/evidence/serving_bench.json
