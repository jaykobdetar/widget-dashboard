# Widget Dashboard — Build Specification

This document specifies a desktop application to be built. It is the complete
description: an implementing agent should need nothing else. Read it
end-to-end before starting. Where details are unspecified, choose sensibly
and document the choice; do not invent features not described here.

---

## 1. What the program is

A dashboard application that turns one monitor of a multi-monitor Linux
desktop into a dedicated control surface for the other monitors. The user
arranges widgets on a grid; widgets do things like control audio, list
windows on the other monitors, embed a terminal, watch for system events,
and run user-defined commands. The user can add new widgets by writing or
generating small plugin packages and dropping them in.

## 2. Target environment (firm)

- Linux, Ubuntu (current LTS) or compatible
- GNOME desktop, X11 session (not Wayland — required for window
  introspection across applications)
- PipeWire audio (PulseAudio compatibility layer present)
- Three monitors typical; the dashboard occupies one of them
- Python 3.11+, modern browser engine for the UI (Chromium-based)

Do not target Wayland, macOS, or Windows. The architecture relies on X11
window management and won't work elsewhere without rewriting whole
subsystems.

## 3. High-level architecture

Three processes, all running as the user:

1. **Backend** — Python FastAPI server on `localhost:8765`. Hosts widgets,
   serves the frontend, exposes REST + per-instance WebSockets.
2. **Frontend** — HTML/CSS/JS application served by the backend, opened in
   Chromium `--app` mode pinned fullscreen to the dashboard monitor.
3. **Lockdown daemon** — separate Python process; watches X for new windows
   and bounces non-allowlisted ones off the dashboard monitor.

`ttyd` (an existing tool) is launched per terminal-widget instance for the
embedded shell. No other top-level processes.

Communication:
- Frontend ↔ backend: REST for actions, one WebSocket per widget instance
  for live data
- Backend ↔ lockdown daemon: UNIX socket
- Backend ↔ system: subprocess calls (`wpctl`, `wmctrl`, `xdotool`, etc.)
  and python-xlib for low-level X work

## 4. The widget plugin contract

Widgets are the unit of functionality. The mixer, window list, terminal,
clock, etc. are all widgets, with the same interface as user/AI-authored
ones. There is no special case for "built-in" except their location on disk.

### 4.1 Folder layout

A widget is a folder containing:

```
<widget-id>/
  widget.json     REQUIRED  manifest
  backend.py      REQUIRED  defines class Widget(WidgetBase)
  frontend.js     REQUIRED  ES module, default-exports { mount }
  style.css       OPTIONAL  widget styles
  settings.js     OPTIONAL  settings UI (default-exports { mount })
  README.md       OPTIONAL
  assets/         OPTIONAL
```

### 4.2 Manifest schema (widget.json)

```json
{
  "id": "cpu-graph",
  "name": "CPU Graph",
  "description": "Per-core CPU usage over time.",
  "version": "1.0.0",
  "author": "...",
  "category": "system",
  "instance_mode": "multi",
  "default_size": { "w": 4, "h": 3 },
  "min_size":     { "w": 2, "h": 2 },
  "max_size":     { "w": 12, "h": 6 },
  "well_known_sizes": [
    { "w": 2, "h": 2, "name": "compact" },
    { "w": 4, "h": 3, "name": "default" }
  ],
  "visibility": "pinned",
  "communication": "free_form",
  "event_sources": [],
  "host_services": [],
  "requires": { "commands": [], "python": [] },
  "permissions": {
    "subprocess": false,
    "network": false,
    "filesystem_read":  [],
    "filesystem_write": []
  },
  "icon": "assets/icon.svg"
}
```

Field semantics:
- `id` — lowercase hyphenated, stable, unique
- `category` — free-form string; suggested values: `system`, `audio`,
  `windows`, `media`, `productivity`, `info`, `custom`. Picker tabs derive
  from distinct categories.
- `instance_mode` — `"singleton"` (one allowed, no per-instance settings)
  or `"multi"` (any number, each independently configured)
- `default_size`/`min_size`/`max_size` — in grid cells (12-col grid)
- `well_known_sizes` — sizes the widget renders best at; UI may soft-snap
- `visibility` — `"pinned"` (default), `"hidden_until_triggered"` (placed
  but not shown until trigger fires), or `"overlay"` (transient pop-over)
- `communication` — `"free_form"` (arbitrary JSON over websocket) or
  `"state_intents"` (declared state + intent schemas; see 4.5)
- `event_sources` — which of `dbus`, `process`, `window`, `file`, `timer`,
  `command` the widget subscribes to
- `host_services` — which of `windows`, `clipboard`, `drag`, `layout` the
  widget calls
- `requires` — hard dependencies; widgets with unmet `commands` are shown
  in the picker as unavailable with a reason
- `permissions` — declared capabilities, shown to the user before they
  trust a widget. Not enforced as a sandbox in v1.

### 4.3 Backend interface

A widget's `backend.py` defines exactly one class `Widget` subclassing
`WidgetBase`:

```python
from widget_dashboard.widget_base import WidgetBase

class Widget(WidgetBase):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def on_message(self, msg: dict) -> None: ...
    async def on_settings_change(self, new_settings: dict) -> None: ...
```

All four are optional; override what you need. Constructor receives a `ctx`
which becomes `self.ctx`. The dashboard creates one instance per widget
placement (multi) or one shared instance (singleton), calls `start()`,
shuttles messages, calls `stop()` on removal.

The `ctx` object exposes:

- `ctx.instance_id: str` — unique id of this placement
- `ctx.settings: dict` — current per-instance settings (read; don't mutate)
- `ctx.state_dir: Path` — per-instance scratch dir for large/disposable
  state (graph history, caches)
- `ctx.log` — logger scoped to this instance
- `ctx.send(msg: dict)` — push to this instance's frontend over its WS
- `await ctx.fire(payload: dict = None)` — report that the trigger fired
  (see 4.6); the only notification call a widget makes
- `ctx.host.windows / clipboard / drag / layout` — shared host services
  (see 5); only those declared in `host_services` are accessible
- `await ctx.events.subscribe(source, match, handler)` — subscribe to a
  system event source (see 4.6); only sources declared in `event_sources`

A widget **never** touches the HTTP server, other instances, or dashboard
internals directly. Everything goes through `ctx`. Privileged actions go
through `ctx.host.*` rather than raw subprocess calls wherever a host
service covers them — this is what makes the future per-widget subprocess
sandbox a drop-in change.

### 4.4 Frontend interface

`frontend.js` is an ES module with a default export:

```js
export default {
  mount(container, api) {
    // container: DOM element the widget owns
    // api.settings: current settings snapshot
    // api.send(msg): send to backend
    // api.onMessage(handler): register handler; returns unsubscribe fn
    // Return a cleanup function (runs on unmount).
  }
}
```

Rules:
- Vanilla DOM; no required framework, no build step
- No `localStorage`/`sessionStorage`/cookies; persist via the backend
- Style with classes in `style.css`; consume theme tokens
  (`--wd-fg`, `--wd-fg-dim`, `--wd-bg`, `--wd-panel`, `--wd-accent`,
  `--wd-font-mono`, `--wd-font-ui`); do not hardcode colors

`settings.js` (optional) is the same shape, with `api.save(newSettings)`
and `api.cancel()` instead of `send`/`onMessage`. The dashboard opens it
in a side panel when the user clicks the gear icon on a widget.

### 4.5 The two communication patterns

A widget picks one:

- **`free_form`** (default): backend and frontend exchange arbitrary JSON.
  Use for terminals, embedded iframes, custom drawing, streaming logs.
- **`state_intents`**: backend maintains a state object; frontend renders
  from state; user actions send named intents. Declared schemas in the
  manifest (`state_schema`, `intents`). Use for normal display/control
  widgets. Backend exposes `get_initial_state()` and `on_intent(type,
  payload)`; frontend uses `api.onState(handler)` and `api.intent(type,
  payload)`. Either pattern is first-class.

### 4.6 Triggers and event sources

A widget may declare event-source subscriptions and react to system events
to drive a trigger. Governing principle:

> **The widget owns the trigger. The dashboard owns the response.**

The widget says *what to watch for* and emits a fire event when it
matches. It does NOT decide importance or presentation. The user
configures the response (toast, badge, sound, flash, overlay, reveal,
switch-to-tab) per instance in the dashboard.

Event sources (`source` values for `ctx.events.subscribe`):

- `dbus` — subscribe to a D-Bus signal (e.g.
  `org.freedesktop.Notifications`, MPRIS, login1)
- `process` — process matching a pattern starts or stops
- `window` — window with given WM_CLASS / title appears, closes, focuses
- `file` — inotify on a path
- `timer` — interval or clock-time
- `command` — runs a command on an interval; fires when its output changes
  or matches a condition

To report a trigger fired:

```python
await ctx.fire(payload={"sender": "alice@x.com", "subject": "..."})
```

The widget specifies no severity and no presentation. The dashboard looks
up the per-instance response config and renders it, templating from the
payload as needed (e.g. toast text `"New mail from {sender}"`).

Response config is stored alongside the instance in the profile:

```json
{
  "id": "inst_mail1",
  "widget_id": "mail-notify",
  "x": 0, "y": 0, "w": 2, "h": 1,
  "settings": { /* widget's own */ },
  "response": {
    "badge": true,
    "toast": { "enabled": true, "text": "New mail from {sender}" },
    "sound": { "enabled": false },
    "flash": false,
    "overlay": false,
    "reveal": false,
    "switch_to_tab": false
  }
}
```

A global `default_response` in config seeds the response config at
placement time; the user edits down. Precedence is two-level: instance
response is authoritative; global supplies starting values only. There is
no per-widget-type middle layer.

## 5. Host services (`ctx.host.*`)

Shared dashboard capabilities. Widgets must declare which they use in
`host_services`.

### 5.1 `windows`

```python
await ctx.host.windows.launch(command, monitor=None, geometry=None,
                               workspace=None)
await ctx.host.windows.list()      # all windows + their monitors
await ctx.host.windows.focus(window_id)
await ctx.host.windows.move(window_id, monitor=N)
```

Uses `wmctrl` and python-xlib. Cooperates with the lockdown daemon: a
launch onto the dashboard monitor is allowlisted for the launch window.

### 5.2 `clipboard`

```python
await ctx.host.clipboard.set_text("...")
await ctx.host.clipboard.set_files(["/path/a.png"])  # real files via xclip
```

`set_files` uses the MIME targets a file manager expects, so Ctrl-V in
Nautilus pastes the file itself.

### 5.3 `drag`

```python
await ctx.host.drag.start_files(["/path/a.png"])
```

Invokes a native drag helper (small bundled GTK tool) because a web page
cannot initiate a native XDND drag. The drag is performed by the helper,
not by the browser cursor; this is a documented rough edge.

### 5.4 `layout`

```python
await ctx.host.layout.reveal()    # show self if hidden
await ctx.host.layout.hide()
await ctx.host.layout.collapse()  # shrink to minimal state
```

For a widget managing its **own** visibility. NOT for grabbing attention —
attention always routes through `ctx.fire` and the response config.

There is no `ctx.host.notify` and no severity concept anywhere in widget
code. Widgets only fire; the dashboard responds.

## 6. Tabs, profiles, and run-states

A **profile** is a named widget layout. Profiles are surfaced as a **tab
bar**: each tab IS a profile (tab = UI, profile = stored object). Clicking
a tab switches which layout is on screen.

Tabs have three states:

- **Disabled** — not running. Widget backends stopped, no polling, no
  event subscriptions, no triggers, no notifications. Costs nothing.
  Default.
- **Enabled** — running in the background but not rendered. Widget
  backends run, triggers fire, can notify per response config.
- **Selected** — running and rendered. Exactly one at a time.

Rules:

- The selected tab is always running (you can't view a stopped tab).
- Selecting a **disabled** tab is a temporary view-time override. When you
  switch away, it reverts to disabled. Peeking does not leave it running.
- Selecting an **enabled** tab is normal — it stays enabled on switch-away.
- On launch every tab starts disabled. Enabled-state is never persisted;
  the user re-enables what they want each session. Nothing runs in the
  background unless explicitly enabled this session.

Editing:

- Auto-save: every change persists to the selected tab's profile
  immediately. There is no "discard."
- Editing a disabled tab you've peeked at still saves its layout; only its
  *run-state* reverts on switch-away, not its contents.
- Edit mode toggles drag/resize handles, gear/close buttons on widgets,
  and an "+ add widget" affordance.

UI affordances on tabs:

- Click tab → select
- Click the run-state dot → toggle enabled/disabled (the selected tab's
  dot is a no-op; you can't disable what you're viewing)
- `+` button → create new tab (prompts for name; new tab is empty)
- Right-click tab → rename / duplicate / delete / reorder

## 7. Monitor lockdown

A separate Python daemon watches the X root for new windows and, if a new
window's primary monitor is the dashboard monitor and its WM_CLASS isn't
allowlisted, moves it to the configured fallback monitor.

Implemented via python-xlib subscription to `_NET_CLIENT_LIST` changes.
The allowlist is in `config.yaml`; the dashboard's own Chromium PID is
added at launch so the daemon never touches it. The daemon emits events
over a UNIX socket so the dashboard can show a toast when it bounces a
window.

Bounce-detection: if the same window-id is moved three times in 10s, stop
moving it (some apps remember position; we yield rather than fight).

Pause: the dashboard has a "pause lockdown for N minutes" button which
sends a message over the UNIX socket; useful when intentionally placing
something on the dashboard monitor.

## 8. Default widgets to ship

These ship in `widgets_builtin/` and use the same plugin interface as
external widgets.

- **`clock`** (multi) — time and date, 12/24h toggle, seconds toggle.
  Reference widget for the simplest possible end-to-end. **Already
  exists in the foundation.**
- **`mixer`** (singleton) — PipeWire mixer via `wpctl`/`pw-cli`. Per-app
  sliders + mute, output/input device pickers, live VU meters via
  `pactl subscribe`. Falls back to `pactl` when PipeWire absent.
- **`window-inventory`** (singleton) — live list of all windows on
  monitors other than the dashboard monitor. Each row: app icon, title,
  monitor number. Click → focus. Right-click → move to monitor / close.
- **`terminal`** (multi) — `ttyd` spawned per instance, embedded as
  iframe. Settings: shell, working directory, font size. Backend
  supervises the `ttyd` process.
- **`shell-command`** (multi) — runs a configured command and shows its
  output. Settings: command, refresh interval (or `on-click`/`on-event`),
  display type (`text`/`number`/`sparkline`/`pill`/`table`), optional
  regex/expression parser, optional thresholds for pill colors.
- **`system-stats`** (singleton) — CPU/RAM/swap/disk/network, each as a
  small sparkline + current value. Reads `/proc` and `/sys`.

## 9. Reference widget designs (do not necessarily ship)

These are validated against the plugin contract; build only if asked, but
the spec must support each:

- **App launcher** (multi): single icon launches a configured Linux app
  (.desktop or binary). Left-click launches; right-click opens settings
  (target app, monitor, size). Uses `ctx.host.windows`.
- **Python script runner** (multi): a `shell-command` configured with
  `python3 /path/script.py`. No new widget needed.
- **File manager** (multi): pinned files/folders. **Click** copies to
  clipboard (real file, via `ctx.host.clipboard.set_files`). **Drag**
  invokes the native drag helper (via `ctx.host.drag.start_files`). The
  click/drag split is the agreed handling of the web-page drag-out limit.
- **Notification widget** (singleton, trigger-driven): subscribes via
  `event_sources: ["dbus"]` to `org.freedesktop.Notifications` (and/or
  mail sources); on match, calls `ctx.fire(payload={...})`. Specifies no
  severity and no presentation.
- **Trigger pop-up** (any visibility, trigger-driven): `visibility:
  "hidden_until_triggered"` + `event_sources: ["process"]`. On fire,
  the user's configured response reveals the widget / shows a toast /
  switches tabs.

## 10. Packaging & install

### 10.1 The `.wdwidget` format

A zip with widget files **at the root** (not nested), named
`<id>.wdwidget`:

```
disk-free.wdwidget
  ├── widget.json
  ├── backend.py
  ├── frontend.js
  ├── style.css
  └── settings.js
```

Distinct extension (not `.zip`) so the download watcher recognises widgets
unambiguously.

### 10.2 Validation

Before install, the dashboard:
1. Confirms valid zip
2. Confirms `widget.json` at root and parses
3. Confirms `backend.py` and `frontend.js` exist
4. Confirms `id` is well-formed and either new or a version update
5. Extracts to temp, reads manifest
6. **Presents declared `permissions`, `host_services`, and `requires` to
   the user and waits for explicit confirmation**

Only after confirmation does the folder move into
`~/.local/share/widget-dashboard/widgets/<id>/` and the registry rescan.

### 10.3 "Install next download"

A one-click flow:

1. User clicks "Install next download"
2. Dashboard begins watching `XDG_DOWNLOAD_DIR` (fallback `~/Downloads`)
   for new files matching `*.wdwidget`
3. Watch is **single-shot** and **time-boxed** (~2 minutes default).
   First matching file → grabbed; nothing → quiet timeout. Never a
   permanent watcher.
4. Match: file is **moved** to the widgets `incoming/` dir; standard
   validation + permission-confirm flow runs.
5. On confirm: install completes, registry rescans, picker refreshes.

Constraints are deliberate: a permanent watcher would auto-install any
`.wdwidget` ever dropped in Downloads (drive-by attack). Single-shot +
time-boxed + explicit confirm gates this safely.

### 10.4 Exporting

The dashboard can produce a `.wdwidget` from any installed widget by
zipping its folder contents at the root.

## 11. Layout, persistence, paths

### 11.1 Filesystem

```
~/.config/widget-dashboard/
  config.yaml         # global settings (dashboard monitor, lockdown
                      # allowlist, default response template)
  profiles/<n>.json   # one per tab
~/.local/share/widget-dashboard/widgets/<id>/
                      # user-installed widgets
~/.local/state/widget-dashboard/
  widget-state/<inst_id>/   # per-instance persistent state
  log/
```

Built-in widgets ship inside the package install location and are scanned
alongside user widgets.

### 11.2 Profile file format

```json
{
  "version": 1,
  "name": "work",
  "grid": { "columns": 12, "row_height": 60 },
  "instances": [
    {
      "id": "inst_a1b2",
      "widget_id": "mixer",
      "x": 0, "y": 0, "w": 6, "h": 4,
      "settings": {},
      "response": {}
    }
  ]
}
```

Per-instance state (graph buffers, history) lives in
`~/.local/state/widget-dashboard/widget-state/<inst_id>/` — NOT in the
profile, because state is large and disposable. Switching tabs does not
restore previous live state; loading a tab gives fresh instances of the
listed widgets.

### 11.3 Grid

12-column gridstack-style grid. Row height ~60px. Drag to move, drag
corners to resize. Edit-mode toggle reveals handles and per-widget chrome
(gear, close). Auto-save on any change.

## 12. Process management & launch

Three `systemd --user` units:

- `widget-dashboard-backend.service` — FastAPI backend
- `widget-dashboard-lockdown.service` — X window-guard daemon
- `widget-dashboard-ui.service` — Chromium `--app` window

UI depends on backend; lockdown is independent. Each survives the others
crashing.

Chromium command:

```
chromium-browser \
  --app=http://localhost:8765 \
  --window-position=<X>,<Y> \
  --window-size=<W>,<H> \
  --start-fullscreen \
  --user-data-dir=$HOME/.local/state/widget-dashboard/chromium-profile \
  --no-first-run --disable-features=TranslateUI --disable-infobars
```

Monitor X/Y/W/H are computed at start-time from `xrandr` based on the
configured dashboard monitor.

## 13. Honest constraints (must be respected, not "fixed")

These are properties of the architecture; they are not bugs.

- **File drag-OUT of the web page** to native apps is impossible from
  Chromium `--app`. Resolution is the click-copies / drag-via-native-
  helper split documented in 9 (File manager).
- **Window introspection requires X11.** Wayland-on-GNOME is not
  supported and won't be without rewriting whole subsystems.
- **Widgets share one backend process in v1.** No sandbox. The plugin API
  is structured so a future per-widget subprocess sandbox is a drop-in
  change — but it is not v1's job.
- **The dashboard is localhost-only.** No remote access by design.
- **Notification response is dashboard-defined, not widget-defined.** A
  widget only emits `ctx.fire`; it specifies no severity, no presentation.
  This is a deliberate split of responsibility, not a limitation.

## 14. Aesthetic direction (for the frontend)

"Instrument panel." Dark warm near-black background, brass/amber accent
(`#d9a441`), tabular monospace for data, serif for chrome labels. Run-state
dots glow when live. Restrained and utilitarian — this is a control
surface stared at for hours, not a landing page. Theme tokens are exposed
as CSS custom properties (`--wd-*`) so widgets consume them; no hardcoded
colors.

## 15. What already exists ("foundation slice")

A working foundation already implements:

- The backend shell: registry, instance manager, profile/tab store,
  per-instance WebSockets, dashboard orchestrator with the full tab
  state machine including peek-revert
- The frontend shell: tab bar with run-state dots, gridstack grid,
  widget host that lazy-loads each widget's `frontend.js`, edit mode,
  picker
- The clock widget as the reference plugin
- `.wdwidget` pack/validate/install via a `packaging` module and a
  `wdwidget` CLI
- `systemd` user units and a Chromium launch script
- A one-command `run.sh` (venv + deps + launch)

Build on top of this; do not replace it unless redesigning a subsystem.
The plugin contract, tab-state model, fire-based trigger model, and host
service shape are settled and verified — do not change them.

## 16. Build order (suggested)

1. **`shell-command` widget** — exercises per-instance settings and the
   gear UI, unlocking lots of user value without new system integration.
2. **Settings UI host** — the side panel that loads each widget's
   `settings.js`. Currently the gear button is a no-op.
3. **Host services + event sources scaffolding** — implement the `ctx.host.*`
   service registry, the event bus, source implementations starting with
   `timer` (simplest) and `process` (powers triggers).
4. **`system-stats` widget** — proves the graph-over-time shape with no
   external dependencies.
5. **`mixer` widget** — first real system integration via `wpctl`.
6. **`window-inventory` widget** — via `wmctrl`, paving the way for
   lockdown.
7. **Lockdown daemon** — separate process, X-watcher, UNIX socket to the
   backend.
8. **`terminal` widget** — `ttyd` supervision per instance.
9. **Trigger response rendering** — toast/badge/sound/flash/overlay/reveal/
   switch-to-tab pipeline, per-instance response config UI.
10. **Notification + trigger-popup widgets** — exercise the full trigger
    pipeline end-to-end.
11. **Install-next-download flow** — inotify watcher (single-shot,
    time-boxed) + confirm dialog.
12. **App launcher and file manager widgets** — using `windows`, `clipboard`,
    `drag` host services.

Stop after each step and verify it runs before continuing.

## 17. Definition of done (overall)

The program is complete when:

- A user on Ubuntu GNOME/X11 with three monitors can run the dashboard on
  one monitor and see it occupy that monitor exclusively (lockdown active).
- They can arrange widgets in a grid via drag/resize, switch between
  multiple tab-profiles, enable a tab to keep it watching in the
  background, and the peek-revert rule works for disabled tabs.
- The default widgets in section 8 all function: mixer controls real
  audio, window inventory reflects real windows and clicks focus them,
  terminal embeds a working shell, shell-command runs and displays.
- A user can write or AI-generate a `.wdwidget`, drop it in (or use
  "install next download"), see the permission dialog, accept, and have
  the widget appear in the picker.
- A trigger-driven widget (e.g. process-launch popup) actually fires when
  its watched event occurs, and the user's configured response (toast,
  reveal, switch-to-tab) executes.
- Restarting the dashboard restores all profiles and their layouts, with
  every tab starting disabled (selected tab is auto-running).

Tests at each step go through the real HTTP routes and assert observable
behavior, not just imports — that's how the foundation was built and that
is the standard for everything after.
