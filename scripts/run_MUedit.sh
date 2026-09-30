#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/python"

# `uv run` syncs the locked environment and executes inside it, so the app
# launches from a bare checkout with no activation step.  Falling back to a
# plain `python` keeps the script working inside an already-activated
# environment (conda, venv) and when uv is not installed.
if command -v uv >/dev/null 2>&1 && [[ "${MUEDIT_NO_UV:-0}" != "1" ]]; then
  PY=(uv run --project "$ROOT_DIR" python)
else
  PY=(python)
fi

export PYTHONPATH="$BACKEND_DIR/src:${PYTHONPATH:-}"
export MUEDIT_HOST="${MUEDIT_HOST:-127.0.0.1}"
export MUEDIT_PORT="${MUEDIT_BACKEND_PORT:-8000}"
export MUEDIT_OPEN_BROWSER="${MUEDIT_OPEN_BROWSER:-1}"

cd "$BACKEND_DIR"

# One server: the API, and the page at / on the same origin.
"${PY[@]}" -m muedit.cli api &
SERVER_PID=$!
echo "MUedit started (PID $SERVER_PID) on http://127.0.0.1:$MUEDIT_PORT/"

cleanup() {
  echo "Stopping MUedit..."
  [[ -n "${SERVER_PID:-}" ]] && kill "$SERVER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [[ "$MUEDIT_OPEN_BROWSER" == "1" ]]; then
  "${PY[@]}" - <<PY
import urllib.request, time, sys, os, webbrowser

port = os.environ.get("MUEDIT_PORT", "8000")
deadline = time.time() + 60
while time.time() < deadline:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as r:
            if r.status == 200:
                webbrowser.open(f"http://127.0.0.1:{port}/")
                break
    except Exception:
        pass
    time.sleep(0.5)
else:
    print("Timed out waiting for MUedit to start", file=sys.stderr)
PY
fi

wait "$SERVER_PID" >/dev/null 2>&1 || true
