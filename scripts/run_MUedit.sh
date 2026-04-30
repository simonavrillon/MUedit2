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
python -m http.server "$MUEDIT_FRONTEND_PORT" 2>/dev/null &
FRONT_PID=$!
echo "Frontend started (PID $FRONT_PID) on :$MUEDIT_FRONTEND_PORT"

cleanup() {
  echo "Stopping services..."
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" >/dev/null 2>&1 || true
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [[ "$MUEDIT_OPEN_BROWSER" == "1" ]]; then
  python - <<PY
import urllib.request, time, sys, os, webbrowser

backend_port  = os.environ.get("MUEDIT_PORT", "8000")
frontend_port = os.environ.get("MUEDIT_FRONTEND_PORT", "8080")
deadline = time.time() + 60

def wait_for(url, label):
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    print(f"Timed out waiting for {label}", file=sys.stderr)
    return False

if wait_for(f"http://localhost:{backend_port}/api/v1/health", "backend") and \
   wait_for(f"http://localhost:{frontend_port}/", "frontend"):
    webbrowser.open(f"http://localhost:{frontend_port}/")
PY
fi

wait "$BACK_PID" >/dev/null 2>&1 || true
wait "$FRONT_PID" >/dev/null 2>&1 || true
