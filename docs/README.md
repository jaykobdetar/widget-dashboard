# Widget Dashboard docs

## Building a widget (the main thing)

**[widgets.md](widgets.md)** is a complete, self-contained guide to writing a
widget. Hand it to an AI (or read it yourself) along with a description of the
widget you want — it covers the folder layout, the full manifest reference, the
backend (`WidgetBase` + `ctx`) and frontend (`mount` + `api`) contracts, host
services, events/triggers, the rules that matter, install/test/package steps,
and a complete copy-paste example. Nothing else needs to be read to build one.

## Running / installing the app

See **[../BUILD.md](../BUILD.md)** — venv + deps, `run.sh`, the systemd units,
the test suite, and system dependencies per widget.

## Reference (kept in sync with the implementation)

| Doc | What |
|---|---|
| [host-services.md](host-services.md) | `ctx.host.*`: windows / clipboard / drag / layout |
| [triggers.md](triggers.md) | event sources + the widget-fires / dashboard-responds model |
| [default-widgets.md](default-widgets.md) | the widgets that ship, and reference designs |
| [layout.md](layout.md) | grid, tabs & run-states, explicit save/load + presets |
| [packaging.md](packaging.md) | the `.wdwidget` format and install flow |
| [lockdown.md](lockdown.md) | keeping the dashboard monitor exclusive (X11 guard) |
| [launch.md](launch.md) | startup, autostart, Chromium launch |

## Design rationale (historical)

These describe the original design from the spec phase. The canonical spec is
[../SPEC.md](../SPEC.md); where these predate the built implementation, the
reference docs above and `widgets.md` are authoritative.

- [architecture.md](architecture.md), [environment.md](environment.md),
  [constraints.md](constraints.md), [open-questions.md](open-questions.md)
- [authoring-guide.md](authoring-guide.md) — merged into [widgets.md](widgets.md)

## A note on accuracy

The implementation deliberately diverges from `SPEC.md` in two places the user
requested: the aesthetic is light/modern (SPEC §14 describes the original dark
"instrument panel"), and persistence is explicit save/load + presets (SPEC §6
describes auto-save). The docs above reflect what the code actually does.
