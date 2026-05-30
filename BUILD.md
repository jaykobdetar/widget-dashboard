# Building & running the Widget Dashboard

This is the full Widget Dashboard built to `SPEC.md`. The foundation slice (the
runnable shell + clock widget proving the plugin contract) has been built out
into the complete program: the default widgets, host services, event sources,
the trigger/response pipeline, packaging/install, and the lockdown daemon.

## What's implemented

**Backend (`backend/widget_dashboard/`)**
- FastAPI surface: registry, instance manager, profile/tab store, per-instance
  websockets, a **system websocket** for shell-level events, config, packaging,
  and lockdown control routes (`app.py`)
- The full **tab run-state model** — disabled / enabled / selected, peek-revert,
  all-disabled-on-launch — plus rename / duplicate / delete / reorder
  (`dashboard.py`, `profiles.py`)
- **Host services** (`ctx.host.*`): `windows`, `clipboard`, `drag`, `layout`,
  scoped to each widget's declared `host_services` (`host_services.py`)
- **Event sources** (`ctx.events`): `timer`, `process`, `window`, `file`,
  `command`, `dbus`, scoped to declared `event_sources` (`events.py`)
- **Trigger → response pipeline**: `ctx.fire()` → per-instance response config
  (instance-authoritative, global default as seed) → templated broadcast to the
  shell (`dashboard.py`, `config.py`)
- **Packaging**: pack / validate / upload+confirm install / export, and the
  single-shot, time-boxed "install next download" watcher
  (`packaging.py`, `download_watch.py`)
- **Lockdown daemon**: standalone X window-guard + UNIX socket, with the
  dashboard-side client that degrades quietly when the daemon is absent
  (`lockdown.py`)

**Default widgets (`backend/widget_dashboard/widgets_builtin/`)**
- `clock` — reference widget (free-form) + settings.js
- `shell-command` — text / number / sparkline / pill / table (state_intents) + settings.js
- `system-stats` — CPU / mem / swap / disk / net sparklines from `/proc`
- `mixer` — per-app sliders + device pickers via `pactl` (state_intents)
- `window-inventory` — windows on the other monitors via the `windows` host
  service; click focuses, right-click moves/closes (state_intents)
- `terminal` — one `ttyd` per instance, embedded as an iframe + settings.js
- `clipboard-history` — recent clipboard text via the `clipboard` host service;
  click to copy back, ★ to pin (state_intents)
- `notepad` — a sticky-note text pad that autosaves as you type (free-form, multi)

**Frontend (`frontend/`)**
- Tab bar with run-state dots, drag-reorder, right-click rename/duplicate/delete
- Gridstack grid, edit mode, per-widget chrome (settings / response / remove)
- Categorised widget picker with capability badges and unavailable reasons
- Right-hand **settings drawer** (hosts each widget's `settings.js`) and a
  per-instance **trigger-response editor**
- System-event rendering: toasts, widget badges, flash, reveal, overlay, sound,
  switch-to-tab, plus lockdown bounce notices
- Install dialog (permission-confirm), upload, and "install next download"

## Run it (X11 GNOME session)

```bash
./run.sh              # venv + deps + backend on http://localhost:8765
./run.sh --ui 1       # also pin the Chromium app window to monitor index 1
```

Then open `http://localhost:8765` in a browser to review, or use `--ui`.

The lockdown daemon is a **separate** process (it can move windows, so run it
deliberately on a real multi-monitor session):

```bash
cd backend && . .venv/bin/activate
python -m widget_dashboard.lockdown
```

## Tests

Behavioural tests go through the real HTTP + WebSocket routes (SPEC §17):

```bash
cd backend && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q
```

They cover the registry, the tab state machine (incl. peek-revert), instance
lifecycle, both communication patterns over websockets, the trigger pipeline,
config, packaging install, and host/event scoping.

## Install as services (optional)

```bash
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now widget-dashboard-backend
systemctl --user enable --now widget-dashboard-lockdown
systemctl --user enable --now widget-dashboard-ui
```

## System dependencies

`pactl` (mixer), `wmctrl` + `xdotool` + `xprop` (windows/lockdown), `xclip`
(clipboard), `ttyd` (terminal), `dbus-monitor` (notification source). Widgets
whose `requires.commands` are missing show in the picker as unavailable with a
reason rather than breaking.

## Adding a widget (the whole point)

Drop a folder in `~/.local/share/widget-dashboard/widgets/<id>/` with
`widget.json`, `backend.py` (a `Widget` subclass of `WidgetBase`),
`frontend.js`, and optional `style.css` / `settings.js`; click **rescan**, or
package it as a `.wdwidget` and use **install**. The built-in widgets are the
reference to copy. See `docs/widgets.md` for the full contract.
