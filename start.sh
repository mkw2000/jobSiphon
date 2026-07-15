#!/usr/bin/env bash
# start.sh — check/start Ollama, then run the job discovery pipeline.
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

# ── 1. Require ollama binary ───────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    echo "ERROR: ollama not found in PATH."
    echo "Install it from https://ollama.com then pull a model:"
    echo "  ollama pull mistral"
    exit 1
fi

# ── 2. Start Ollama server if not already running ─────────────────────────
if curl -sf "$OLLAMA_API" &>/dev/null; then
    echo "✓ Ollama is already running."
else
    echo "Starting Ollama server in the background…"
    ollama serve >/tmp/ollama-serve.log 2>&1 &
    OLLAMA_PID=$!
    echo "  PID: $OLLAMA_PID  (logs: /tmp/ollama-serve.log)"

    # Wait up to 10 s for the server to become ready
    for i in $(seq 1 10); do
        if curl -sf "$OLLAMA_API" &>/dev/null; then
            echo "✓ Ollama is ready."
            break
        fi
        sleep 1
    done

    if ! curl -sf "$OLLAMA_API" &>/dev/null; then
        echo "WARNING: Ollama did not respond within 10 s. Proceeding anyway…"
    fi
fi

# ── 3. Require uv environment ─────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv is required. Install it from https://docs.astral.sh/uv/"
    exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: Python environment not found. Run first:"
    echo "  make setup"
    exit 1
fi

# ── 4. Run the pipeline ────────────────────────────────────────────────────
echo ""
echo "Starting job discovery pipeline…"
echo "──────────────────────────────────────────────"
uv run python main.py "$@"
