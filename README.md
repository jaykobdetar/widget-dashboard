# Widget Dashboard

A dedicated dashboard application that turns a secondary monitor into a unified
control surface for the rest of the desktop. Runs fullscreen on its assigned
monitor; the user picks widgets from a library and arranges them on a grid.
New widgets are added by writing (or having an AI write) a small plugin module
and dropping it into the widgets directory.

## Status

Built to `SPEC.md`. Backend, the eight default widgets, host services, event
sources, the trigger/response pipeline, packaging/install, the lockdown daemon,
and the full frontend are implemented and tested through the real HTTP routes.
See `BUILD.md` to run it and `docs/` for the design rationale.

## Goals

- Replace the current second-monitor setup (taskbar + sound mixer + terminal)
  with a single integrated dashboard.
- Provide a widget library covering the user's day-one needs: audio mixer,
  window inventory across the other monitors, embedded terminal.
- Let the user add new widgets cheaply by having an AI write a plugin
  conforming to a clear interface.
- Prevent any program other than the dashboard from opening on monitor 2.
- Pick widgets from the library and arrange them on a draggable, resizable
  grid; settings UIs are owned by each widget.

## Non-goals

- Cross-platform support. Linux + GNOME on X11 only.
- Replacing the system shell or window manager. This lives on top of GNOME.
- Remote access. Everything binds to localhost.
- A declarative widget config language. Widgets are real code, not YAML.

## Mental model

The dashboard itself is a **shell** that:
1. Discovers installed widgets (each is a folder with backend + frontend code)
2. Shows a picker so the user can add widget instances to the grid
3. Routes data between each instance's backend and frontend
4. Persists the layout and per-instance settings as named profiles,
   surfaced as a tab bar — each tab is a profile (a full layout), and
   switching tabs switches the whole arrangement (e.g. "Work", "Music")

The mixer, window list, and terminal are not special — they ship as default
widgets in the same plugin format that user/AI-authored widgets use.

## Inspiration

The user-facing model is heavily inspired by Android 16's lock-screen
widget hub and the Jetpack Glance / Remote Compose work shown at I/O
2026: a pickable widget library, a resizable grid, per-widget
declared sizes, and a clean separation between widget state and user
intents. Specific borrowings are noted inline in the relevant docs.

## Documents

Start here:

- **`docs/widgets.md`** — **the** complete, self-contained guide to building a
  widget (hand it to an AI + a description and it can produce a working plugin)
- `BUILD.md` — how to run, test, and install the app
- `docs/README.md` — index of all docs and how to read them

Reference (kept current with the implementation):

- `docs/host-services.md` — shared capabilities widgets call (`ctx.host.*`)
- `docs/triggers.md` — event sources and the trigger/response model
- `docs/default-widgets.md` — the widgets that ship + reference designs
- `docs/layout.md` — grid, tabs/run-states, explicit save/load + presets
- `docs/packaging.md` — `.wdwidget` format and install flow
- `docs/lockdown.md` — keeping the dashboard monitor exclusive
- `docs/launch.md` — startup, autostart, service files

Design rationale (historical; the spec phase — `SPEC.md` is the canonical spec):

- `docs/architecture.md`, `docs/environment.md`, `docs/constraints.md`,
  `docs/open-questions.md`
- `docs/authoring-guide.md` — merged into `docs/widgets.md`

## License

[MIT](LICENSE) © 2026 Jaykob Detar
