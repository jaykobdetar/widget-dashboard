# Host services

> Canonical, copy-paste API reference for widget authors lives in
> **[widgets.md §8](widgets.md)**. This page is the design rationale plus the
> same API; if the two ever disagree, `widgets.md` is correct.

Some capabilities shouldn't be reimplemented by every widget: placing a
window on a specific monitor, copying a file to the clipboard, telling
the user something happened. These live in the dashboard and are offered
to widgets through the `ctx` object as **host services**.

This keeps widgets small and consistent — especially AI-authored ones,
which should reach for a documented host call instead of shelling out to
`wmctrl` themselves and getting it subtly wrong.

A widget must declare which host services it uses in its manifest, so
the user can see them before enabling it:

```json
"host_services": ["windows", "clipboard"]
```

## `windows` — window placement and control

Wraps the same X machinery the lockdown daemon and window-inventory
widget use.

```python
await ctx.host.windows.launch(
    command="gimp",
    monitor=1,                # which monitor to place it on
    geometry={"x": 0, "y": 0, "w": 1200, "h": 900},  # optional
    workspace=None,           # optional
)
await ctx.host.windows.list()              # real windows + monitor + icon
await ctx.host.windows.focus(window_id)
await ctx.host.windows.close(window_id)
await ctx.host.windows.move(window_id, monitor=2)
await ctx.host.windows.set_geometry(window_id, x, y, w, h)
await ctx.host.windows.maximize(window_id)
await ctx.host.windows.fullscreen(window_id)   # toggle
await ctx.host.windows.set_window_state(window_id, "above")  # "sticky"; action=add|remove|toggle
ctx.host.windows.dashboard_monitor()       # int (config value), NOT async
```

`list()` returns `{id, title, wm_class, monitor, geometry:{x,y,w,h}, icon}` per
real window (docks/desktop windows filtered out); `icon` is a PNG data-URL from
`_NET_WM_ICON` or `null`. Geometry comes from **python-xlib** (accurate absolute
coordinates; `wmctrl` is a fallback). `dashboard_monitor()` is a config value,
not auto-detected, so don't rely on it to identify the dashboard's screen — the
window-inventory widget shows all monitors uniformly.

The launcher widget uses `launch(...)` with its configured monitor/size
options. Because placement goes through the host, it cooperates with
the lockdown daemon (a launch onto monitor 2 can be auto-allowlisted for
the moment it's intended).

## `clipboard` — clipboard access

```python
await ctx.host.clipboard.set_text("some text")
await ctx.host.clipboard.set_files(["/home/me/a.png"])  # file URIs + paths
```

`set_files` puts the file on the clipboard with the MIME targets a file
manager expects, so a subsequent paste in Nautilus pastes the actual
file. Backed by `xclip` (X11) with the right targets.

## `drag` — native drag source

The web layer can't initiate a native drag onto other applications (see
`docs/constraints.md`). The host bridges this with a small native helper.

```python
await ctx.host.drag.start_files(["/home/me/a.png"])
```

Intended to invoke a native drag helper (e.g. `dragon` or a bundled GTK tool)
that becomes the XDND drag source. **The helper is not bundled in this build,
so this call currently raises `RuntimeError`** with a message pointing to
click-to-copy (`clipboard.set_files`). Treat drag-out as unavailable and use
the clipboard path (see `docs/constraints.md`).

## `fire` — report that a trigger fired

This is not under `ctx.host`; it's `ctx.fire(...)` directly, because it's
central enough to the trigger model to be a first-class call. A widget
calls it to report that its watched condition occurred, with an optional
payload. It does **not** specify severity or presentation — the
dashboard renders the response configured for this instance.

```python
await ctx.fire(payload={"sender": "alice@example.com", "subject": "..."})
```

See `docs/triggers.md` for the full model. There is deliberately no
`notify(severity=...)` service — widgets don't rank importance or pick
presentation.

## `layout` — self visibility only

Lets a widget change *its own* presence on the dashboard. This is for a
widget managing itself (e.g. collapsing when idle), not for grabbing the
user's attention — attention is routed through `ctx.fire` and the
dashboard's response config.

```python
await ctx.host.layout.reveal()   # show self if hidden
await ctx.host.layout.hide()     # hide self
await ctx.host.layout.collapse() # shrink to a minimal state
```

Whether a fired trigger reveals the widget, flashes it, or switches to
its tab is decided by the instance's response config (see
`docs/triggers.md`), not by the widget calling these directly. A widget
*may* still call `reveal()`/`hide()` for its own reasons, but it should
not use them as a notification channel.

## Why route everything through `ctx.host`

Besides consistency, this is what makes the future subprocess sandbox
(see open questions) viable: if every privileged action is already an
`await ctx.host.*` call, those calls become RPC across a process
boundary with no change to widget code. Widgets that shell out directly
would break under sandboxing; widgets that use host services won't.
