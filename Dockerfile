# syntax=docker/dockerfile:1
# PRAHARI gateway image: the API and the built website from one non-root container.
#   docker build -t prahari-gateway .
#   docker-compose -f infra/docker-compose.yml --profile container up -d   # gateway on :8001
# Three stages: the site (Node), the Python environment (uv, no dev tools), and a slim runtime
# that only copies their results. The embedding model is baked in, so the container needs no
# internet at start. Model servers stay on the host (PRAHARI_MODEL_HOST=host.docker.internal).

# ---- 1. the website
FROM node:22.22.3-bookworm-slim AS site
WORKDIR /site
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. the Python environment
FROM python:3.12.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/opt/models
WORKDIR /app/backend
# dependencies first: this layer is reused until the lockfile changes
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/prahari ./prahari
RUN uv sync --frozen --no-dev
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

# ---- 3. runtime
FROM python:3.12.13-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 prahari \
 && useradd --system --uid 10001 --gid prahari --home-dir /app --shell /usr/sbin/nologin prahari
WORKDIR /app
COPY --from=build --chown=prahari:prahari /app/backend /app/backend
COPY --from=build --chown=prahari:prahari /opt/models /opt/models
COPY --chown=prahari:prahari backend/config /app/backend/config
COPY --chown=prahari:prahari docs /app/docs
COPY --chown=prahari:prahari README.md DESIGN.md /app/
COPY --from=site --chown=prahari:prahari /site/dist /app/frontend/dist
RUN mkdir -p /app/var/run && chown -R prahari:prahari /app/var
# production by default: admin key required, chaos refused, JSON logs
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/opt/models \
    PRAHARI_ENV=production \
    PRAHARI_LOG_FORMAT=json
USER prahari
EXPOSE 8000
# liveness for Docker; orchestrators should also use /readyz for traffic
HEALTHCHECK --interval=15s --timeout=3s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
WORKDIR /app/backend
CMD ["uvicorn", "prahari.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10", "--no-server-header"]
