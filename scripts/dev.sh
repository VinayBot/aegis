#!/usr/bin/env bash
# Brings up the whole AEGIS system on one Mac and tears it down on Ctrl-C.
#   Redis Stack + Jaeger (Docker via Colima) -> mlx_lm.server (primary) -> Ollama (fallback,
#   Llama Guard) -> LiteLLM proxy -> vLLM (Metal) -> the gateway (FastAPI, dev mode, chaos on)
#   -> the website (Vite). The LiteLLM proxy and vLLM start when `make setup-serving` installed them.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/var/run"
LOG="$ROOT/var/log"
mkdir -p "$RUN" "$LOG"
MLX_MODEL="${AEGIS_MLX_MODEL:-mlx-community/Qwen2.5-3B-Instruct-4bit}"
OLLAMA_MODEL="${AEGIS_OLLAMA_MODEL:-llama3.2:3b}"
pids=()

say() { printf '\033[36m[aegis]\033[0m %s\n' "$*"; }
wait_for() { # url name seconds
  for _ in $(seq 1 "$3"); do curl -s -m 1 -o /dev/null "$1" && return 0; sleep 1; done
  say "$2 did not come up in $3 s (see var/log)"; return 1
}
cleanup() {
  say "shutting down"
  for pid in "${pids[@]}"; do kill -CONT "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
  rm -f "$RUN"/*.pid
}
trap cleanup EXIT INT TERM

# 1. side systems
if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null; then say "starting Colima (2 CPU, 2 GB)"; colima start --cpu 2 --memory 2 >/dev/null; fi
fi
say "starting Redis Stack + Jaeger"
docker-compose -f "$ROOT/infra/docker-compose.yml" up -d >/dev/null 2>&1 || docker compose -f "$ROOT/infra/docker-compose.yml" up -d

# 2. primary model: mlx_lm.server (we own this process, so the Chaos Lab can SIGSTOP it)
if ! curl -s -m 1 -o /dev/null http://127.0.0.1:8081/v1/models; then
  MLX_BIN="$(command -v mlx_lm.server || echo "$HOME/.local/bin/mlx_lm.server")"
  say "starting $MLX_BIN ($MLX_MODEL)"
  "$MLX_BIN" --model "$MLX_MODEL" --host 127.0.0.1 --port 8081 >"$LOG/mlx.log" 2>&1 &
  echo $! >"$RUN/mlx.pid"; pids+=($!)
  wait_for http://127.0.0.1:8081/v1/models "mlx_lm.server" 180
else
  pgrep -f "mlx_lm.server.*8081" | head -1 >"$RUN/mlx.pid" || true
fi

# 3. fallback model: Ollama
if ! curl -s -m 1 -o /dev/null http://127.0.0.1:11434/api/tags; then
  say "starting ollama serve"; ollama serve >"$LOG/ollama.log" 2>&1 & pids+=($!)
  wait_for http://127.0.0.1:11434/api/tags "ollama" 30
fi
ollama list | grep -q "^${OLLAMA_MODEL}" || { say "pulling $OLLAMA_MODEL"; ollama pull "$OLLAMA_MODEL"; }
ollama list | grep -q "^llama-guard3:1b" || { say "pulling llama-guard3:1b (content safety)"; ollama pull llama-guard3:1b; }
# keep the fallback warm so the first failover is not a cold load
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\":\"$OLLAMA_MODEL\",\"keep_alive\":\"30m\"}" >/dev/null || true

# 3b. the real LiteLLM proxy server (make setup-serving), one backend in a chain
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-aegis-local}"   # local only; the gateway reads it from the env
if [ -x "$HOME/.venv-litellm-proxy/bin/litellm" ] && ! curl -s -m 1 -o /dev/null http://127.0.0.1:4000/health/liveliness; then
  say "starting the LiteLLM proxy on :4000"
  LITELLM_TELEMETRY=False "$HOME/.venv-litellm-proxy/bin/litellm" --config "$ROOT/infra/litellm/config.yaml" \
    --host 127.0.0.1 --port 4000 >"$LOG/litellm-proxy.log" 2>&1 & pids+=($!)
  wait_for http://127.0.0.1:4000/health/liveliness "LiteLLM proxy" 60 || true
fi

# 3c. real vLLM (V1 engine, paged attention, continuous batching) on Metal via vllm-metal.
#     A 0.5B model so it fits beside the other two on 16 GB; AEGIS_VLLM=0 skips it.
if [ "${AEGIS_VLLM:-1}" = "1" ] && [ -x "$HOME/.venv-vllm-metal/bin/vllm" ] && ! curl -s -m 1 -o /dev/null http://127.0.0.1:8090/v1/models; then
  say "starting vLLM (Metal) on :8090"
  VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 "$HOME/.venv-vllm-metal/bin/vllm" serve mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --host 127.0.0.1 --port 8090 --max-model-len 4096 --served-model-name qwen2.5-0.5b >"$LOG/vllm.log" 2>&1 & pids+=($!)
  wait_for http://127.0.0.1:8090/v1/models "vLLM" 240 || true
fi

# 4. the gateway
say "starting the gateway on :8000"
# AEGIS_ENV=dev: loopback callers are the local operator (the site needs no key) and chaos may run.
# Without it the gateway is in production mode: admin key required, chaos refused.
(cd "$ROOT/backend" && AEGIS_ENV=dev AEGIS_CHAOS_ENABLED=1 uv run uvicorn aegis.main:app \
  --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5 >"$LOG/gateway.log" 2>&1) &
pids+=($!); echo $! >"$RUN/gateway.pid"
wait_for http://127.0.0.1:8000/api/health "gateway" 90

# 5. the website
say "starting the website on :5173"
(cd "$ROOT/frontend" && npm run dev >"$LOG/vite.log" 2>&1) & pids+=($!)
wait_for http://127.0.0.1:5173 "website" 30

say "ready"
say "  site      http://127.0.0.1:5173"
say "  API docs  http://127.0.0.1:8000/api/swagger"
say "  traces    http://127.0.0.1:16686"
say "Ctrl-C stops everything this script started."
wait
