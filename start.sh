#!/usr/bin/env bash
# Railway single-container entrypoint: starts the FastAPI backend and the
# Streamlit frontend side-by-side and forwards SIGTERM to both so Railway's
# graceful-shutdown flow works correctly.
set -euo pipefail

API_PORT="${API_PORT:-8000}"
UI_PORT="${PORT:-8501}"          # Railway injects $PORT for the public service

# Export so the Streamlit app's _ensure_backend_running() shim is skipped --
# it detects EXPILOT_API_URL and exits early, avoiding the subprocess dance.
export EXPILOT_API_URL="http://127.0.0.1:${API_PORT}"

# Persist SQLite data to Railway's mounted volume when available.
export EXPILOT_DB_PATH="${RAILWAY_VOLUME_MOUNT_PATH:-/app}/exppilot.db"

cleanup() {
    echo "[start.sh] shutting down..."
    kill "$API_PID" "$UI_PID" 2>/dev/null || true
    wait "$API_PID" "$UI_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

echo "[start.sh] Starting API on :${API_PORT}"
uvicorn api.main:app --host 0.0.0.0 --port "$API_PORT" &
API_PID=$!

# Give the API a moment to bind before Streamlit's first health-check fires.
sleep 2

echo "[start.sh] Starting UI on :${UI_PORT}"
streamlit run ui/app.py \
    --server.address 0.0.0.0 \
    --server.port "$UI_PORT" \
    --server.headless true \
    --browser.gatherUsageStats false &
UI_PID=$!

# Wait for either process to exit; if one crashes, bring the other down.
wait -n "$API_PID" "$UI_PID" 2>/dev/null || true
cleanup
