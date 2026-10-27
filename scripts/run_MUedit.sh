#!/usr/bin/env bash
# Starts MUedit from a terminal (macOS, Linux) and opens it in the browser; quit with Ctrl+C.
set -euo pipefail

export MUEDIT_OPEN_BROWSER="${MUEDIT_OPEN_BROWSER:-1}"
exec uv run --project "$(cd "$(dirname "$0")/.." && pwd)" muedit api
