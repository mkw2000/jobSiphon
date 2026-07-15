#!/usr/bin/env bash
# start.sh — validate the configured AI provider, then run JobSiphon.
set -euo pipefail

OLLAMA_API="http://localhost:11434/api/tags"
OLLAMA_PID=""  # set below only if this script starts Ollama itself

# ── Cleanup: stop Ollama server if this script started it ─────────────────
# trap EXIT fires on normal exit, errors (set -e), AND Ctrl-C.
cleanup() {
    if [[ -n "$OLLAMA_PID" ]]; then
        echo ""
        echo "Stopping Ollama server (PID $OLLAMA_PID)…"
        kill "$OLLAMA_PID" 2>/dev/null || true
        echo "✓ Ollama stopped."
    fi
}
trap cleanup EXIT

# ── 1. Require the uv-managed environment ─────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv is required. Install it from https://docs.astral.sh/uv/"
    exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: Python environment not found. Run first:"
    echo "  make setup"
    exit 1
fi

# ── 2. Validate or start the selected provider ─────────────────────────────
LLM_PROVIDER="$(uv run python -c 'from llm_client import provider_name; print(provider_name())')"
if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    if ! command -v ollama &>/dev/null; then
        echo "ERROR: LLM_PROVIDER=ollama but ollama is not installed."
        exit 1
    fi
    if curl -sf "$OLLAMA_API" &>/dev/null; then
        echo "✓ Ollama is already running."
    else
        echo "Starting Ollama server in the background…"
        ollama serve >/tmp/ollama-serve.log 2>&1 &
        OLLAMA_PID=$!
        for _ in $(seq 1 10); do
            curl -sf "$OLLAMA_API" &>/dev/null && break
            sleep 1
        done
        curl -sf "$OLLAMA_API" &>/dev/null || {
            echo "ERROR: Ollama did not become ready."
            exit 1
        }
    fi
else
    uv run python - <<'PY'
from llm_client import provider_status
status = provider_status(timeout=3.0)
if not status["configured"]:
    raise SystemExit("ERROR: FREELLMAPI_UNIFIED_API_KEY is missing from .env or ~/.env")
if not status["online"]:
    raise SystemExit("ERROR: FreeLLM API is not responding at the configured base URL")
print(f"✓ FreeLLM API is online ({len(status['models'])} models, route={status['model']}).")
PY
fi

# ── 3. Run the pipeline ────────────────────────────────────────────────────
echo ""
echo "Starting job discovery pipeline…"
echo "──────────────────────────────────────────────"
uv run python main.py "$@"
