.PHONY: perf sweep setup infra dev test test-backend test-frontend e2e lint typecheck build serve record-fixture memory assets clean

setup: ## install everything (Python deps, spaCy model, Node deps, Playwright browser, models)
	cd backend && uv sync --extra litellm  # LiteLLM is the optional adapter; installed so it can be demonstrated
	cd frontend && npm install && npx playwright install chromium
	uv tool install --python 3.12 mlx-lm
	ollama pull llama3.2:3b

infra: ## Redis Stack + Jaeger only
	docker-compose -f infra/docker-compose.yml up -d

dev: ## the whole system: infra, MLX primary, Ollama fallback, gateway, website
	./scripts/dev.sh

test: test-backend test-frontend ## unit + integration tests (needs `make infra` for Redis)

test-backend:
	cd backend && uv run pytest -q

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
	cd backend && AEGIS_CHAOS_ENABLED=1 uv run uvicorn aegis.main:app --port 8000

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
