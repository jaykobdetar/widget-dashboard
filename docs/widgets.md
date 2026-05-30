# Writing a widget for Widget Dashboard

This document is **everything you need to build a working widget**. It is
written to be self-contained: hand it to an AI (or a developer) along with a
description of the widget you want, and that is enough to produce a complete,
working plugin. No other file needs to be read.

> **If you are an AI generating a widget:** read this whole document, then
> output a folder named after the widget id containing `widget.json`,
> `backend.py`, `frontend.js`, and (if it has styles or settings) `style.css`
> and `settings.js`. Follow the contracts exactly — the field names, method
> names, and message shapes here are the real ones the host uses. Copy the
> "Complete worked example" at the end as a starting template. Obey the
> "Rules that matter" section; they encode bugs we have already hit and fixed.

---

## 1. What a widget is

The Widget Dashboard is a dashboard of widgets on a grid. A **widget** is a folder
with a small backend (Python) and frontend (vanilla JS). The mixer, clock,
system-stats, terminal, etc. are all widgets using the exact same contract as
anything you write — there is no "built-in" special case beyond location on
disk.

Each placement of a widget on the grid is an **instance**. A widget can be a
**singleton** (one instance allowed) or **multi** (any number, each with its
own settings). The host:

- discovers widgets at startup and on a user "Rescan",
- creates one backend object per instance and runs its lifecycle,
- serves the frontend and connects each instance to its backend over a
  WebSocket,
- persists each instance's layout + settings in the active tab's profile.

The backend runs inside the dashboard's Python process (one shared process, no
sandbox — see §12). It reaches the outside world only through the `ctx` object
it is given; it must never import or touch the HTTP server, other instances, or
dashboard internals directly.

---

## 2. Folder layout

```
<widget-id>/
  widget.json     REQUIRED  manifest (metadata + declared capabilities)
  backend.py      REQUIRED  defines `class Widget(WidgetBase)`
  frontend.js     REQUIRED  ES module, default-exports { mount }
  style.css       optional  styles (loaded globally — namespace your classes!)
  settings.js     optional  settings UI; ES module, default-exports { mount }
  assets/         optional  icons/images, served at /api/widgets/<id>/assets/...
  README.md       optional  human description
```

`<widget-id>` and the manifest `id` must match and be lowercase, hyphenated,
unique (e.g. `disk-free`, `cpu-graph`).

---

## 3. The manifest (`widget.json`)

```json
{
  "id": "disk-free",
  "name": "Disk Free",
  "description": "Free space on a chosen mount, as a colored pill.",
  "version": "1.0.0",
  "author": "you",
  "category": "system",
  "instance_mode": "multi",
  "communication": "state_intents",
  "default_size": { "w": 3, "h": 2 },
  "min_size":     { "w": 2, "h": 1 },
  "max_size":     { "w": 6, "h": 4 },
  "visibility": "pinned",
  "event_sources": [],
  "host_services": [],
  "requires": { "commands": [] },
  "permissions": { "subprocess": false, "network": false,
                   "filesystem_read": [], "filesystem_write": [] },
  "icon": "assets/icon.svg"
}
```

Field reference (these are the fields the host actually reads):

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | lowercase-hyphenated, stable, unique; must equal the folder name |
| `name` | yes | display name in the picker and widget chrome |
| `description` | yes | one line shown in the picker |
| `version` | rec. | semver string |
| `author` | no | free text |
| `category` | rec. | groups widgets into picker tabs. Common: `system`, `audio`, `windows`, `media`, `productivity`, `info`, `custom`. Any string works. |
| `instance_mode` | yes | `"singleton"` or `"multi"` |
| `communication` | rec. | `"free_form"` (default) or `"state_intents"` — see §5 |
| `default_size` | yes | `{w,h}` in 12-column grid cells (row height ≈ 60px) |
| `min_size` | no | `{w,h}`, default `{1,1}` |
| `max_size` | no | `{w,h}`, omit for no cap |
| `visibility` | no | `"pinned"` (default), `"hidden_until_triggered"` (placed but hidden until revealed — see §8/§9), or `"overlay"` |
| `event_sources` | no | which system event sources you subscribe to: any of `timer`, `process`, `window`, `file`, `command`, `dbus`. **You can only subscribe to sources you list here.** See §9 |
| `host_services` | no | which shared services you call: any of `windows`, `clipboard`, `drag`, `layout`. **You can only use services you list here.** See §8 |
| `requires.commands` | no | external CLI tools you need. If any is missing from `PATH`, the widget appears in the picker as *unavailable* with a reason (it is not loaded) |
| `permissions` | no | declarative; shown to the user before they trust/install the widget. **Not enforced as a sandbox** — purely informational. Keep it honest. |
| `icon` | no | path under `assets/`; shown in the picker |

Notes:
- The manifest is JSON, so no comments and no trailing commas.
- Fields from older drafts that are **not** used by the host and should be
  omitted: `well_known_sizes`, `state_schema`, `intents`. Declare your state
  and intents in code, not the manifest.

---

## 4. The backend (`backend.py`)

Define exactly one class named `Widget`, subclassing `WidgetBase`:

```python
from widget_dashboard.widget_base import WidgetBase

class Widget(WidgetBase):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def on_message(self, msg: dict) -> None: ...
    async def on_settings_change(self, new_settings: dict) -> None: ...
```

- Always `import` the base via the full package path
  `from widget_dashboard.widget_base import WidgetBase` (an `isinstance` check in
  the loader depends on it).
- All four methods are optional — override only what you need. They are all
  `async`.
- The constructor is provided by `WidgetBase` and receives the context; do not
  redefine `__init__`. If you need init logic, do it in `start()`.

### The `ctx` object (`self.ctx`)

Everything the backend may do goes through `ctx`:

| Member | Type | Use |
|---|---|---|
| `ctx.instance_id` | `str` | unique id of this placement |
| `ctx.settings` | `dict` | this instance's current settings (read-only; don't mutate) |
| `ctx.state_dir` | `pathlib.Path` | per-instance scratch dir for large/disposable state (graph history, caches). Already created. **Not** for settings. |
| `ctx.log` | `logging.Logger` | logger scoped to this instance |
| `ctx.send(msg: dict)` | method | push a free-form JSON message to this instance's frontend (Pattern A) |
| `ctx.set_state(state: dict)` | method | publish new backend state to the frontend (Pattern B) |
| `await ctx.fire(payload: dict = None)` | coroutine | report that this widget's trigger fired (§9). The widget never decides presentation. |
| `ctx.host.<service>` | object | shared dashboard services you declared in `host_services` (§8) |
| `await ctx.events.subscribe(source, match, handler)` | coroutine | subscribe to a system event source you declared in `event_sources` (§9) |

Lifecycle the host drives:

1. instance created → `start()` is awaited,
2. frontend messages/intents arrive → `on_message()` / `on_intent()`,
3. user edits settings → `on_settings_change(new_settings)` (and `ctx.settings`
   is updated for you before the call),
4. instance removed, or its tab stops running → `stop()` is awaited. Cancel
   your tasks and kill subprocesses here. (Event subscriptions are torn down
   for you automatically.)

A tab only runs while it is selected or explicitly enabled, so a backend may be
started and stopped many times in a session. Make `start()`/`stop()`
idempotent-safe and don't leak tasks.

---

## 5. The two communication patterns

Pick one in the manifest (`communication`). Both are first-class.

### Pattern A — `free_form` (default)

Backend and frontend exchange arbitrary JSON. The backend pushes with
`ctx.send(msg)`; the frontend receives with `api.onMessage(handler)` and sends
with `api.send(msg)`, handled by `on_message`.

Use for: terminals, embedded iframes, custom drawing, streaming logs, anything
that isn't naturally "current state + named actions".

### Pattern B — `state_intents`

The backend owns a **state** object; the frontend renders from it and sends
named **intents** for user actions. Add two methods to your `Widget`:

```python
async def get_initial_state(self) -> dict:
    """Return the current state for a newly-connected frontend."""

async def on_intent(self, intent_type: str, payload: dict) -> None:
    """Handle a named action from the frontend."""
```

Publish state changes with `ctx.set_state(new_state)`. The frontend uses
`api.onState(handler)` and `api.intent(type, payload)`.

Use for: normal display/control widgets (mixer, window list, a stat readout).
It's the easier pattern to get right.

You do **not** declare state/intent schemas in the manifest — they live in code.

---

## 6. The frontend (`frontend.js`)

An ES module with a default export providing `mount`:

```js
export default {
  mount(container, api) {
    // container: the DOM element this instance owns (already in the grid)
    // ...build your UI inside container...
    // return a cleanup function, called on unmount.
    return () => { /* remove listeners, timers, etc. */ };
  },
};
```

The `api` object:

| Member | Pattern | Use |
|---|---|---|
| `api.settings` | both | snapshot of current settings at mount time (read-only) |
| `api.send(msg)` | A | send a free-form message to the backend |
| `api.onMessage(fn)` | A | register a handler for backend messages; returns an unsubscribe fn |
| `api.onState(fn)` | B | register a handler for state updates; returns an unsubscribe fn |
| `api.intent(type, payload)` | B | send a named intent to the backend |

The host **buffers the latest state/message and replays it the moment you
subscribe**, so you always get current data on mount regardless of timing — you
do not need to request initial state yourself.

Rules:

- **Vanilla DOM, no build step.** No framework is required. If you really want
  one, bundle it into a single `frontend.js`; it's loaded as a plain ES module.
- **No `localStorage` / `sessionStorage` / cookies.** Persist anything that
  should survive via settings (user-editable) or `ctx.state_dir` (backend).
- **Styles are global, not shadow-scoped.** Namespace every class with a short
  widget prefix (e.g. `.df-root`, `.df-value`) so you don't collide with the
  shell or other widgets. The built-ins use prefixes like `mx-`, `ss-`, `sc-`.
- **Consume theme tokens; never hardcode colors.** Available CSS custom
  properties (already defined on `:root` by the shell):

  ```
  --wd-bg --wd-panel --wd-panel-2 --wd-edge
  --wd-fg --wd-fg-dim
  --wd-accent --wd-accent-strong --wd-accent-soft --wd-on-accent
  --wd-live --wd-danger
  --wd-font-ui --wd-font-mono
  --wd-radius --wd-radius-sm --wd-radius-pill
  --wd-shadow --wd-shadow-lift
  ```

  Use `--wd-accent-strong` (not `--wd-accent`) for small text/thin lines on the
  light background, `--wd-on-accent` for text drawn on an accent fill, and
  `--wd-danger` for errors/over-threshold.

  These tokens are **user-themeable** (a global accent + base color, derived to a
  light or dark scheme) and every card can be **tinted a per-widget color** (the
  🎨 control the shell adds to every widget). So: don't implement your own color
  option, keep backgrounds token-based or transparent (so a card tint shows
  through), and don't hardcode text colors — consume the tokens and your widget
  adapts to any theme/tint automatically.

---

## 7. The settings UI (`settings.js`) — optional

Same shape as the frontend, but the `api` differs. Opened in a side drawer when
the user clicks the gear on a widget (in any mode — editing is always on).

```js
export default {
  mount(container, api) {
    const s = api.settings || {};
    // build a form...
    form.onsubmit = (e) => { e.preventDefault(); api.save({ /* new settings */ }); };
    cancelBtn.onclick = () => api.cancel();
  },
};
```

- `api.settings` — current settings.
- `api.save(newSettings)` — persists to the instance and triggers the backend's
  `on_settings_change`; the drawer closes.
- `api.cancel()` — close without saving.

Omit `settings.js` for widgets with nothing to configure (e.g. singletons).
Reuse the shell's form classes for a consistent look: `wd-form`, `wd-check`,
`wd-form-actions`, `pill-btn` / `pill-btn primary`, `wd-mini`. Inputs styled by
`.wd-form input/select` automatically.

---

## 8. Host services (`ctx.host.*`)

Shared, privileged dashboard capabilities. **List each one you use in
`host_services`** — accessing an undeclared service raises `PermissionError`.
All methods are `async` unless noted.

### `windows` — window introspection & placement (X11)
```python
mon   = ctx.host.windows.dashboard_monitor()          # int (config value), NOT async
wins  = await ctx.host.windows.list()                 # see shape below
await ctx.host.windows.focus(window_id)
await ctx.host.windows.close(window_id)
await ctx.host.windows.move(window_id, monitor=1)     # send to a monitor index
await ctx.host.windows.set_geometry(window_id, x, y, w, h)   # absolute, for tiling
await ctx.host.windows.maximize(window_id)
await ctx.host.windows.fullscreen(window_id)          # toggle
await ctx.host.windows.set_window_state(window_id, "above")  # also "sticky", action=add|remove|toggle
await ctx.host.windows.launch(command, monitor=None, geometry=None, workspace=None)
```
`list()` returns, per real window (docks/desktop are filtered out):
`{id, title, wm_class, monitor, geometry:{x,y,w,h}, icon, above, sticky,
fullscreen}` where `monitor` is the index it sits on, `icon` is a PNG data-URL
(the window's `_NET_WM_ICON`) or `null`, and `above`/`sticky`/`fullscreen` are
its current EWMH states. Geometry is read via **python-xlib** for accurate absolute
coordinates (with a `wmctrl` fallback). `dashboard_monitor()` is a *config*
value, not auto-detected — group by `monitor` and treat all monitors uniformly
rather than trusting it to identify the dashboard's screen.

### `clipboard`
```python
await ctx.host.clipboard.set_text("hello")
await ctx.host.clipboard.set_files(["/path/a.png"])   # real files (file managers paste the file)
```
Backed by `xclip`.

### `drag`
```python
await ctx.host.drag.start_files(["/path/a.png"])      # currently raises: helper not bundled
```
A web page can't start a native drag-out; this would call a bundled helper that
isn't shipped yet, so it raises a clear error. Use click-to-copy
(`clipboard.set_files`) instead (§13).

### `layout` — a widget managing **its own** visibility
```python
await ctx.host.layout.reveal()     # show self (e.g. a hidden_until_triggered widget)
await ctx.host.layout.hide()
await ctx.host.layout.collapse()
```
This is **not** for grabbing attention — that always goes through `ctx.fire`
and the user's response config (§9).

---

## 9. Events and triggers

> **The widget owns the trigger. The dashboard owns the response.**

A widget watches for something and, when it matches, calls `ctx.fire(payload)`.
It specifies **no** severity and **no** presentation. The user has already
configured, per instance, how the dashboard should react (toast, badge, sound,
flash, full overlay, reveal a hidden widget, switch to that tab). The dashboard
renders that response, templating any toast text from your payload.

### Subscribing to a source
```python
async def start(self):
    await ctx.events.subscribe("process", {"pattern": "obs", "event": "start"},
                               self._on_event)

async def _on_event(self, event: dict):
    await self.ctx.fire(payload={"app": event.get("name", "process")})
```

`subscribe(source, match, handler)` — `handler` is an async fn taking the event
dict. You may only use sources listed in `event_sources`.

Sources and their `match` / event payloads:

| source | match keys | fires handler with |
|---|---|---|
| `timer` | `interval` (seconds) **or** `at` ("HH:MM") | `{now}` |
| `process` | `pattern` (regex on comm), `event` = `start`\|`stop`\|`any`, `interval` (default 3) | `{event, pid, name}` |
| `window` | `wm_class`, `title` (regex), `event` = `appear`\|`close`, `interval` (default 2) | `{event, id, title}` |
| `file` | `path`, `interval` (default 2) | `{path, exists}` on change |
| `command` | `command`, `interval` (default 30), `on` = `change`\|`match`, `regex` (for `match`) | `{output, ...}` |
| `dbus` | `interface`, `member`, `type` = `signal`\|`method_call`, `bus` = `session`\|`system` | `{raw}` (raw `dbus-monitor` block) |

### Firing
```python
await ctx.fire(payload={"sender": "alice@x.com", "subject": "Hi"})
```
The user's toast template can reference payload keys, e.g. `"New mail from
{sender}"`. `{__widget__}` is also available (the widget id). Missing keys are
left as-is, never crash.

A trigger widget that should stay hidden until it fires sets `"visibility":
"hidden_until_triggered"` and relies on the user enabling the "reveal" response.

---

## 10. Rules that matter (read before shipping)

These are not style preferences; each one is a bug we hit and fixed.

1. **Patch the DOM, don't rebuild it.** Build your structure once on first
   state, then update text nodes / attributes / widths in place. Do **not** set
   `container.innerHTML = ...` on every update — it drops event handlers, resets
   form fields, and visibly stutters. (See the mixer/system-stats frontends.)
2. **Never fight the user's input.** If you have a slider/input the user can
   drag, don't overwrite its value from a live update while they're interacting
   (track a "pointer down" flag and skip writes until release).
3. **Prefer events over polling.** If you must poll, use a modest interval and
   only push when the value actually changed (compare to the last value) — the
   host re-renders on every push.
4. **Clean up in `stop()`.** Cancel `asyncio` tasks, terminate subprocesses,
   close streams. The instance can be stopped/started repeatedly.
5. **Don't block the event loop.** Use `await asyncio.create_subprocess_exec`,
   not blocking `subprocess.run`; `await asyncio.sleep`, not `time.sleep`.
6. **Tolerate missing tools/permissions.** If `requires` isn't guaranteed,
   catch `FileNotFoundError`/`RuntimeError` and surface a friendly message in
   state instead of crashing.
7. **Theme tokens only, namespaced classes only** (see §6).

---

## 11. Installing, testing, packaging

**During development:**
1. Put your folder in `~/.local/share/widget-dashboard/widgets/<id>/`.
2. In the dashboard, open the `⋯` menu → **Rescan widgets** (this re-imports
   backends and refreshes the picker). It now appears in the picker (or shows a
   reason if `requires` is unmet).
3. Add it from the floating **+** button. Frontend assets are served no-cache
   and cache-busted, so a change shows on reload. A *backend* change to an
   **already-running** instance needs the instance removed & re-added (or the
   backend restarted), since the class is already loaded.

**Packaging to share:** a `.wdwidget` is just a zip of the folder's contents at
the root, named `<id>.wdwidget`. Build one with
`scripts/wdwidget.py pack <dir>`, then install via the `⋯` menu → **Upload a
.wdwidget** (which shows the declared permissions/host_services/requires for
confirmation before installing).

**Verifying behavior** (how the project tests widgets — through the real HTTP
routes): start the backend, add the instance via `POST /api/instances`, connect
to `ws://localhost:8765/api/instances/<id>/ws`, and assert on the
`{"__state__": ...}` frames (state_intents) or raw messages (free_form). See
`backend/tests/test_api.py`.

---

## 12. The honest constraints (don't try to "fix" these)

- **One shared backend process, no sandbox.** Widgets run in-process. The
  `permissions` field is informational, not enforced. Everything going through
  `ctx` keeps a future sandbox a drop-in change — so route privileged work
  through `ctx.host.*`, not raw calls, where a service exists.
- **X11 only** for window introspection (`windows`). Wayland is unsupported.
- **No native file drag-out** from the web UI — use click-to-copy.
- **Localhost only.** No remote access. Don't bind sockets publicly.
- **Notifications are dashboard-defined.** A widget only `fire`s; it never picks
  severity or presentation.

---

## 13. Complete worked example

A `state_intents` widget that shows free disk space on a configurable mount as a
colored pill, refreshing on a timer, and with a settings UI. Copy this shape.

**`disk-free/widget.json`**
```json
{
  "id": "disk-free",
  "name": "Disk Free",
  "description": "Free space on a chosen mount, as a colored pill.",
  "version": "1.0.0",
  "category": "system",
  "instance_mode": "multi",
  "communication": "state_intents",
  "default_size": { "w": 3, "h": 2 },
  "min_size": { "w": 2, "h": 1 },
  "permissions": { "subprocess": false, "filesystem_read": ["/"] }
}
```

**`disk-free/backend.py`**
```python
from __future__ import annotations
import asyncio
import shutil
from widget_dashboard.widget_base import WidgetBase


class Widget(WidgetBase):
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def get_initial_state(self) -> dict:
        return self._sample()

    async def on_intent(self, intent_type: str, payload: dict) -> None:
        if intent_type == "refresh":
            self.ctx.set_state(self._sample())

    async def on_settings_change(self, new_settings: dict) -> None:
        self.ctx.set_state(self._sample())   # reflect new mount immediately

    async def _loop(self) -> None:
        while True:
            self.ctx.set_state(self._sample())
            await asyncio.sleep(float(self.ctx.settings.get("interval", 30) or 30))

    def _sample(self) -> dict:
        mount = self.ctx.settings.get("mount", "/")
        warn = float(self.ctx.settings.get("warn_below_gb", 10) or 10)
        try:
            u = shutil.disk_usage(mount)
            free_gb = round(u.free / 1e9, 1)
            return {"mount": mount, "free_gb": free_gb, "low": free_gb < warn, "error": None}
        except OSError as e:
            return {"mount": mount, "free_gb": None, "low": False, "error": str(e)}
```

**`disk-free/frontend.js`**
```js
export default {
  mount(container, api) {
    container.innerHTML = `
      <div class="df-root" title="click to refresh">
        <div class="df-pill" data-role="pill">…</div>
        <div class="df-mount" data-role="mount"></div>
      </div>`;
    const pill = container.querySelector('[data-role="pill"]');
    const mount = container.querySelector('[data-role="mount"]');

    container.addEventListener("click", () => api.intent("refresh"));

    const off = api.onState((s) => {                // patch in place, no rebuild
      if (s.error) { pill.textContent = "err"; pill.classList.add("df-low"); mount.textContent = s.error; return; }
      pill.textContent = s.free_gb == null ? "…" : `${s.free_gb} GB`;
      pill.classList.toggle("df-low", !!s.low);
      mount.textContent = s.mount;
    });
    return () => off();
  },
};
```

**`disk-free/style.css`**
```css
.df-root { height: 100%; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px; cursor: pointer; }
.df-pill { font-family: var(--wd-font-mono); font-weight: 600;
  padding: 4px 14px; border-radius: var(--wd-radius-pill);
  background: var(--wd-accent); color: var(--wd-on-accent); }
.df-pill.df-low { background: var(--wd-danger); color: #fff; }
.df-mount { font-size: 0.7rem; color: var(--wd-fg-dim); }
```

**`disk-free/settings.js`**
```js
export default {
  mount(container, api) {
    const s = api.settings || {};
    const attr = (v) => (v == null ? "" : String(v).replace(/"/g, "&quot;"));
    container.innerHTML = `
      <form class="wd-form">
        <label>Mount path
          <input name="mount" type="text" value="${attr(s.mount || "/")}" />
        </label>
        <label>Warn below (GB)
          <input name="warn" type="number" min="0" value="${attr(s.warn_below_gb ?? 10)}" />
        </label>
        <label>Refresh (seconds)
          <input name="interval" type="number" min="2" value="${attr(s.interval ?? 30)}" />
        </label>
        <div class="wd-form-actions">
          <button type="button" data-cancel class="pill-btn">Cancel</button>
          <button type="submit" class="pill-btn primary">Save</button>
        </div>
      </form>`;
    const form = container.querySelector("form");
    form.onsubmit = (e) => {
      e.preventDefault();
      api.save({
        mount: form.mount.value.trim() || "/",
        warn_below_gb: Number(form.warn.value) || 10,
        interval: Number(form.interval.value) || 30,
      });
    };
    container.querySelector("[data-cancel]").onclick = () => api.cancel();
  },
};
```

---

## 14. Authoring checklist

- [ ] Folder name == manifest `id` (lowercase-hyphenated).
- [ ] `widget.json` is valid JSON; `default_size`, `instance_mode`,
      `communication` set.
- [ ] `backend.py` defines `class Widget(WidgetBase)` and imports the base from
      `widget_dashboard.widget_base`.
- [ ] state_intents widgets implement `get_initial_state` + `on_intent` and push
      via `ctx.set_state`; free_form widgets use `on_message` + `ctx.send`.
- [ ] Every `ctx.host.*` you call is listed in `host_services`; every
      `ctx.events.subscribe` source is listed in `event_sources`.
- [ ] `frontend.js` default-exports `{ mount }`, patches the DOM in place, and
      returns a cleanup function.
- [ ] CSS uses `--wd-*` tokens and namespaced class names only.
- [ ] `stop()` cancels tasks/kills subprocesses; nothing blocks the event loop.
- [ ] `requires.commands` lists any external tools; `permissions` is honest.
