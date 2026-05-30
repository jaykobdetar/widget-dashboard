#!/usr/bin/env bash
# One-command setup + run for the Widget Dashboard foundation.
# Run this from the repo root (the folder containing this script).
#
#   ./run.sh                  # set up venv, install deps, start the backend
#   ./run.sh --ui 1           # also launch the pinned Chromium window on monitor 1
#   ./run.sh --ui 1 --lockdown  # also start the X window-guard daemon
#
# The lockdown daemon can move windows, so it is opt-in: only start it on a
# real multi-monitor session where you want monitor exclusivity (SPEC §7).
#
# The backend serves both the API and the frontend at http://localhost:8765
# Open that URL in a browser to review, or use --ui to pin it to a monitor.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/backend"

# --- venv + deps -----------------------------------------------------------
if [[ ! -d .venv ]]; then
  echo "[run] creating virtualenv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Only (re)install deps when requirements.txt changed since the last install —
# re-running pip on every launch is a noticeable startup cost. A sentinel holds
# the hash of the last-installed requirements.txt.
REQ_HASH="$(sha256sum requirements.txt | cut -d' ' -f1)"
STAMP=".venv/.deps-hash"
if [[ ! -f "$STAMP" || "$(cat "$STAMP" 2>/dev/null)" != "$REQ_HASH" ]]; then
  echo "[run] installing dependencies..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  echo "$REQ_HASH" > "$STAMP"
else
  echo "[run] dependencies up to date; skipping pip install"
fi

# --- optional UI launch ----------------------------------------------------
UI_MONITOR=""
LOCKDOWN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ui) UI_MONITOR="${2:-1}"; shift 2 ;;
    --lockdown) LOCKDOWN="1"; shift ;;
    *) shift ;;
  esac
done

if [[ -n "$LOCKDOWN" ]]; then
  echo "[run] starting lockdown daemon (X window guard)..."
  python -m widget_dashboard.lockdown &
fi

if [[ -n "$UI_MONITOR" ]]; then
  echo "[run] will launch UI on monitor index $UI_MONITOR once backend is up..."
  (
    # wait for the backend to answer, then launch the pinned window
    for _ in $(seq 1 30); do
      if curl -sf http://localhost:8765/api/tabs >/dev/null 2>&1; then break; fi
      sleep 0.3
    done
    "$HERE/scripts/launch-ui.sh" "$UI_MONITOR"
  ) &
fi

# --- run backend (foreground) ---------------------------------------------
echo "[run] starting backend at http://localhost:8765  (Ctrl-C to stop)"
exec python -m widget_dashboard.app
