#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/python"
FRONTEND_DIR="$ROOT_DIR/frontend"

export PYTHONPATH="$BACKEND_DIR/src:${PYTHONPATH:-}"
export MUEDIT_HOST="${MUEDIT_HOST:-0.0.0.0}"
export MUEDIT_PORT="${MUEDIT_BACKEND_PORT:-8000}"
export MUEDIT_FRONTEND_PORT="${MUEDIT_FRONTEND_PORT:-8080}"
export MUEDIT_OPEN_BROWSER="${MUEDIT_OPEN_BROWSER:-1}"

cd "$BACKEND_DIR"
python -m muedit.cli api &
BACK_PID=$!
echo "Backend started (PID $BACK_PID) on :$MUEDIT_PORT"

cd "$FRONTEND_DIR"
python -m http.server "$MUEDIT_FRONTEND_PORT" &
FRONT_PID=$!
echo "Frontend started (PID $FRONT_PID) on :$MUEDIT_FRONTEND_PORT"

cleanup() {
  echo "Stopping services..."
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" >/dev/null 2>&1 || true
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [[ "$MUEDIT_OPEN_BROWSER" == "1" ]]; then
  sleep 2
  python - <<PY
import webbrowser, os
webbrowser.open(f"http://localhost:{os.environ['MUEDIT_FRONTEND_PORT']}/")
PY
fi

wait "$BACK_PID" >/dev/null 2>&1 || true
wait "$FRONT_PID" >/dev/null 2>&1 || true
