# Launch

## Processes

Three user-level systemd services:

- `widget-dashboard-backend.service` — the FastAPI backend
- `widget-dashboard-lockdown.service` — the X window-guard daemon
- `widget-dashboard-ui.service` — the Chromium `--app` window

Each is a `systemd --user` unit so they live and die with the user
session. The UI service depends on the backend (so it doesn't open
before the page is servable); lockdown is independent.

## Chromium command

```
chromium-browser \
  --app=http://localhost:8765 \
  --window-position=<X>,<Y> \
  --window-size=<W>,<H> \
  --start-fullscreen \
  --user-data-dir=$HOME/.local/state/widget-dashboard/chromium-profile \
  --no-first-run \
  --disable-features=TranslateUI
```

- `--app` removes tabs and the URL bar; the window is bare
- Dedicated user-data-dir keeps the profile separate from normal browsing
  (extensions, cookies, etc. don't leak in)
- `--start-fullscreen` plus `--window-position` on the second monitor
  pins it where we want
- Monitor X/Y/W/H are filled in at service-start time by a small wrapper
  script that parses `xrandr --query` and picks the configured monitor

## Autostart

The user enables the three units once:

```
systemctl --user enable --now widget-dashboard-backend
systemctl --user enable --now widget-dashboard-lockdown
systemctl --user enable --now widget-dashboard-ui
```

After that, login = dashboard. Logout = dashboard goes away.

## First run

On first launch, the backend:
1. Creates `~/.config/widget-dashboard/` if missing, writes a default
   `config.yaml`
2. Detects monitors via `xrandr`, prompts the user (in the UI) to pick
   which is the dashboard monitor and which is the fallback
3. Persists the picks, restarts the lockdown daemon with the new config

The first-run prompt is itself a tiny built-in screen, not a widget.

## Manual control

For debugging, each process can be run in the foreground:

```
python -m widget_dashboard.backend           # backend
python -m widget_dashboard.lockdown          # lockdown daemon
widget-dashboard-launch-ui                   # ui wrapper script
```
