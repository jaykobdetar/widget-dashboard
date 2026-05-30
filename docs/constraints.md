# Constraints

Honest limits of the chosen architecture (dashboard = Chromium `--app`
web page + Python backend, on GNOME/X11). These aren't bugs to fix; they
are consequences of the design, written down so they don't surprise
anyone later.

## File drag-OUT requires a native helper

A web page cannot initiate a native XDND drag onto another application.
Dragging a file from a dashboard widget *onto* GIMP or a file manager on
another monitor does not work through the browser alone.

**Resolution (chosen):** the file-manager widget distinguishes gestures.

- **Click** a pinned file → the host copies it to the clipboard (real
  file, via `ctx.host.clipboard.set_files`). Paste anywhere.
- **Drag** a pinned file → the host invokes the native drag helper (via
  `ctx.host.drag.start_files`), which becomes the actual drag source.

The drag path has a small visual hand-off (the drag is initiated by the
helper, not the browser cursor). Acceptable trade for keeping the rest
of the dashboard as a clean web app.

## Window introspection is X11-only

Listing and moving other apps' windows relies on X11 (`wmctrl`, xlib).
This is why the project targets GNOME-on-Xorg, not Wayland. On Wayland
these features would require a GNOME Shell extension. See
`docs/environment.md`.

## Widgets share one backend process (v1)

No sandbox in v1: a buggy or hostile widget backend can affect the whole
dashboard. Mitigated by (a) all widgets being user/AI-authored, not from
a marketplace, (b) declared permissions shown before enabling, and (c)
the host-services pattern that makes a future per-widget subprocess
sandbox a drop-in change. See `docs/open-questions.md`.

## The dashboard is local-only

Everything binds to localhost. No remote access, by design. Viewing the
dashboard from another machine is out of scope.

## Notification response is dashboard-defined, not widget-defined

By design (`docs/triggers.md`): a widget only reports that its trigger
fired (`ctx.fire` with an optional payload). It specifies no severity and
no presentation. The user configures the entire response — whether and
how to alert — per instance in the dashboard. This is a deliberate
division of responsibility, not a limitation to remove.
