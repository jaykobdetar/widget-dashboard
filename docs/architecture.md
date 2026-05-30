# Architecture

Three processes, all running as the user:

```
                ┌──────────────────────────────────────┐
                │  Chromium --app (fullscreen, mon 2)  │
                │  ┌────────────────────────────────┐  │
                │  │  Frontend shell (HTML/CSS/JS)  │  │
                │  │  - grid + widget host          │  │
                │  │  - widget picker / settings    │  │
                │  │  - lazy-loads widget frontends │  │
                │  └────────┬───────────────────────┘  │
                └───────────┼──────────────────────────┘
                            │ HTTP + WebSocket
                            ▼
                ┌──────────────────────────────────────┐
                │  Backend (FastAPI, localhost:8765)   │
                │  - widget registry (filesystem scan) │
                │  - widget instance manager           │
                │  - widget backend host (sandboxed-   │
                │    ish subprocesses per instance)    │
                │  - layout + settings persistence     │
                └──────────────────────────────────────┘

                ┌──────────────────────────────────────┐
                │  Lockdown daemon (separate process)  │
                │  - watches _NET_CLIENT_LIST          │
                │  - bounces stray windows off mon 2   │
                └──────────────────────────────────────┘
```

`ttyd` is not a separate top-level process — it's spawned and supervised by
the terminal widget's backend when an instance of that widget is created.
Same pattern for any future widget that needs a long-running helper.

## Why three top-level processes

- The **backend** is the shell that hosts widgets. Single source of truth for
  the layout, the registry, and inter-widget routing.
- The **lockdown daemon** is split out because it needs to react in
  milliseconds to new windows and shouldn't share an event loop with the
  HTTP server. It is also restart-independent — the dashboard UI can crash
  and reload without monitor 2 suddenly accepting stray windows.
- The **frontend** runs in a Chromium `--app` window pinned fullscreen to
  monitor 2.

## Widget hosting model

Each widget folder ships a backend half and a frontend half (see
`docs/widgets.md`). The backend creates one instance per placement on the
grid (for multi-instance widgets) or a single shared instance (for
singletons). Instances live as long as they're on the grid; removing the
widget tears the instance down.

The backend exposes:
- `GET /api/widgets` — list of available widgets (from filesystem scan)
- `POST /api/widgets/rescan` — re-scan the widgets directory (manual trigger)
- `POST /api/instances` — create an instance of a widget
- `DELETE /api/instances/{id}` — destroy an instance
- `GET /api/instances/{id}/frontend.js` — serves the widget's frontend bundle
- `WS /api/instances/{id}/ws` — per-instance websocket for live data and
  commands

The frontend shell never knows what a widget does. It only knows how to
host one: load its frontend module, give it a DOM node and a websocket,
let it do whatever.

## Communication

- Frontend shell ↔ backend: REST for layout/registry/instance management,
  one websocket per widget instance for live data.
- Widget frontend ↔ widget backend: message passing over the instance's
  websocket. Schema is the widget's own business.
- Backend ↔ lockdown daemon: a small UNIX socket. The backend tells the
  daemon the current monitor-2 allowlist; the daemon emits events when it
  bounces a window. The daemon's X watch is also the source for the
  `window` (and contributes to the `process`) event sources widgets
  subscribe to — see `docs/triggers.md` — so there is one watcher, not
  one per widget.
- Backend ↔ system: subprocess calls from widget backends, and the host
  services in `docs/host-services.md`. The shell itself doesn't shell out
  to anything except chromium at launch.

## Config and state on disk

```
~/.config/widget-dashboard/
  config.yaml        # global settings (monitor IDs, lockdown allowlist)
  layout.json        # grid layout + per-instance settings (auto-saved)
~/.local/share/widget-dashboard/widgets/
  <widget-id>/       # user-installed and AI-authored widgets live here
~/.local/state/widget-dashboard/
  widget-state/      # per-instance persistent state (graph buffers, etc.)
  log/
```

The application also ships a built-in widgets directory inside its install
location; both directories are scanned at startup and on rescan.
