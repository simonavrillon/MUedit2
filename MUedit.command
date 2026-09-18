#!/usr/bin/env bash
# Double-clickable launcher for macOS.  Finder starts scripts with a minimal
# PATH that misses uv's install directory, so the usual locations are restored
# before handing over to the real launcher.
set -uo pipefail

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"

hold_window() {
  echo
  read -r -p "Press Return to close this window. "
}

if ! command -v uv >/dev/null 2>&1; then
  cat <<'MSG'
MUedit needs uv, which does not appear to be installed.

Paste this line into a Terminal window, let it finish, then double-click
MUedit again:

  curl -LsSf https://astral.sh/uv/install.sh | sh
MSG
  hold_window
  exit 1
fi

./scripts/run_MUedit.sh
status=$?

# 130/143 are Ctrl+C and a closed window: the user quitting, not a failure.
if [[ $status -ne 0 && $status -ne 130 && $status -ne 143 ]]; then
  echo
  echo "MUedit stopped with an error (exit code $status). The messages above"
  echo "say what went wrong."
  hold_window
fi
