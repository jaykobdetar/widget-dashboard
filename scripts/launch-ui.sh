#!/usr/bin/env bash
# Launch the dashboard UI in a borderless Chromium app window, pinned to the
# configured monitor (docs/launch.md). Foundation version: picks the monitor
# by index from xrandr. Later this reads config.yaml for the dashboard monitor.

set -euo pipefail

URL="http://localhost:8765"
PROFILE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/widget-dashboard/chromium-profile"
MONITOR_INDEX="${1:-0}"   # 0-based index into connected outputs

# Find the geometry of the Nth connected monitor from xrandr.
geom=$(xrandr --query \
  | grep ' connected' \
  | sed -n "$((MONITOR_INDEX + 1))p" \
  | grep -oE '[0-9]+x[0-9]+\+[0-9]+\+[0-9]+' || true)

if [[ -z "$geom" ]]; then
  echo "Could not find monitor index $MONITOR_INDEX; falling back to default placement." >&2
  X=0; Y=0; W=1280; H=800
else
  W=${geom%%x*}; rest=${geom#*x}
  H=${rest%%+*}; rest=${rest#*+}
  X=${rest%%+*}; Y=${rest#*+}
fi

# Prefer chromium, fall back to google-chrome.
BIN=$(command -v chromium-browser || command -v chromium || command -v google-chrome || true)
if [[ -z "$BIN" ]]; then
  echo "No Chromium/Chrome binary found." >&2
  exit 1
fi

exec "$BIN" \
  --app="$URL" \
  --user-data-dir="$PROFILE_DIR" \
  --window-position="${X},${Y}" \
  --window-size="${W},${H}" \
  --start-fullscreen \
  --no-first-run \
  --disable-features=TranslateUI \
  --disable-infobars
