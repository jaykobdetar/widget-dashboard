# Default widgets

These ship with the dashboard. They use the same plugin interface as
user/AI-authored widgets — they're just bundled in the install directory
instead of `~/.local/share/widget-dashboard/widgets/`. Treating them as
plugins keeps the shell honest: if a default widget needs a capability
the plugin API doesn't expose, that's a sign the API needs work, not a
sign for a special case.

## `mixer` (singleton)

PipeWire mixer (via PipeWire's PulseAudio-compatible `pactl` interface).
`state_intents` pattern.

- Per-application volume sliders (with a live activity meter per app) and mute
- Output device picker (with a device-type icon) + master slider
- Input device picker + mic slider
- App rows show an avatar, "what's playing", paused state, and a boost zone
  past 100%; scroll-to-adjust on any row
- Live per-app activity meters via `parec --monitor-stream=<index>` (the same
  mechanism pavucontrol uses), emitted ~20×/s on a separate channel
- Event-driven: a persistent `pactl subscribe` wakes a debounced re-snapshot,
  so the UI updates immediately on external changes without constant polling

## `window-inventory` (singleton)

Live list of all windows on monitors *other than* the dashboard monitor.

- Windows are grouped per monitor, each under a **user-defined label** (set in
  the widget's settings, e.g. "Top"/"Bottom"). All monitors are shown and
  treated uniformly — the dashboard's own monitor isn't special-cased (its
  configured index isn't reliably detected).
- Each row shows the window's **icon** (`_NET_WM_ICON`, with a letter fallback
  for apps like Chrome that don't set one), title, and WM_CLASS.
- **Drag** a window from one monitor group onto another to move it; **click**
  focuses; **right-click** → focus / move to monitor / resize-tile
  (maximize, left/right/top/bottom half, center) / keep (always-on-top, on all
  workspaces/sticky, fullscreen — each shown with a ✓ when active) / close.
- Settings can **hide** any monitor from the list (per-monitor checkbox).
- `state_intents`; uses the `windows` host service. Geometry comes from
  python-xlib (accurate absolute coords) and desktop/dock windows are filtered
  out. Rows/sections are keyed-diffed and re-render pauses during a drag or open
  menu (no flicker, no interruption).
- A monitors endpoint (`GET /api/system/monitors`) backs the label settings.

## `terminal` (multi-instance)

Embedded shell, backed by `ttyd`.

- Each instance spawns its own `ttyd` on a free localhost port and
  embeds it in an iframe
- Settings: shell to run (default `$SHELL`), working directory, font size
- Backend supervises the `ttyd` process; restarts it if it dies
- Multi-instance so the user can have e.g. a logs-tailing terminal and a
  general-purpose one side by side

## `clock` (multi-instance)

Time and date, with 12/24-hour and seconds toggles (settings). `free_form`.
The canonical "simplest possible widget" reference for AI authoring.

## `shell-command` (multi-instance)

The generic "run a command, show its output" widget. `state_intents`. Settings:

- Command to run (string)
- Working directory (optional; `~` expands; a missing dir shows a clear error)
- **Launch in a new terminal window** (optional): instead of capturing output,
  the widget becomes a click-to-launch button that opens the command in a new
  terminal emulator (gnome-terminal/konsole/xterm/…), **detached**
  (`start_new_session`) so the program outlives the dashboard. The terminal is
  held open after the command exits.
- Refresh: `interval` (seconds) or `on-click`
- Display type: `text`, `number`, `sparkline`, `pill`, `table` (whitespace
  columns)
- Optional parse `regex` (first capture group becomes the value)
- Optional label, and pill `thresholds` (op/value/color) for color-coding

Editing the command in settings does **not** run it (so a side-effecting
command isn't fired the moment it's typed). Interval mode runs on its schedule;
on-click mode runs when the widget is clicked. This widget is the AI-authoring
fallback: simple readouts often need only a configured `shell-command`, not a
new plugin.

## `system-stats` (singleton)

CPU, GPU, RAM, disk, and network — each a small sparkline + current value, with
inline temperature badges (CPU/GPU/SSD). `free_form`. Reads `/proc` and hwmon;
GPU usage/temp from a persistent `nvidia-smi` loop (NVIDIA) or amdgpu sysfs; the
GPU row hides itself when no GPU is present. Reference implementation for the
"live graph + readout" shape.

> A `notify` (notification-watch) widget previously shipped but was removed.
> The "Notification widget" below remains as a reference design for the trigger
> model — see also the `dbus` event source in `widgets.md` §9.

## `clipboard-history` (singleton)

Remembers recent clipboard text. `state_intents`; uses the `clipboard` host
service (and `xclip` to read).

- Polls the clipboard (~1s) via `xclip -o -t UTF8_STRING` (text only; images
  ignored) and prepends new, de-duplicated entries.
- Click an entry to copy it back; ★ pins it (pinned stay at the top and are
  never evicted); ✕ deletes; "clear" drops everything except pins.
- History persists in the instance's `state_dir`, so it survives tab switches
  and restarts. Capped to 50 entries.
- Privacy note: a clipboard history can capture sensitive text; it stays local
  and the clear/delete actions drop anything you don't want kept.

## `notepad` (multi)

A small sticky-note / text pad. `free_form`.

- A textarea that **autosaves as you type** (debounced) to the instance's
  `state_dir`; content persists across tab switches and restarts.
- Multi-instance, so you can scatter several notes across tabs.
- Tint it like a sticky note with the card color (🎨 in the widget chrome) —
  that's the universal per-widget color, not a notepad-specific setting.

# Reference widget designs

These are sketches of widgets the user wants to be *possible*, used here
to validate the plugin contract. They aren't necessarily shipped in the
box, but each demonstrates a capability the spec must support, and each
makes a good AI-authoring example.

## App launcher (multi-instance)

A single icon that launches a specific Linux application (a `.desktop`
entry or a binary). Not Android APKs — native Linux apps.

- Left-click → launch via `ctx.host.windows.launch(...)`
- Right-click → settings: which application, and launch options —
  target monitor, window size/position, workspace
- `host_services: ["windows"]`
- Demonstrates: host-mediated window placement, right-click settings,
  cooperation with the lockdown daemon when launching onto monitor 2

## Python script runner (multi-instance)

Runs a configured Python script and shows its result.

- Settings: script path, one-shot vs interval, what to display (exit
  code / last stdout line / streamed output)
- A thin specialization of the `shell-command` widget; could literally
  be `shell-command` configured with `python3 /path/script.py`
- Demonstrates: nothing new — included because the user asked, and to
  show that "run a script" doesn't need a bespoke widget

## File manager (multi-instance)

Pinned files and folders the user can drop elsewhere.

- Shows a configurable set of pinned files/folders with icons
- **Click** a file → `ctx.host.clipboard.set_files([...])` (copy the
  real file; paste anywhere)
- **Drag** a file → `ctx.host.drag.start_files([...])` (native drag
  helper becomes the drag source)
- The click/drag split is the agreed resolution to the web-page
  drag-out constraint — see `docs/constraints.md`
- `host_services: ["clipboard", "drag"]`
- Demonstrates: the clipboard and native-drag host services, and an
  honest architectural limit handled gracefully

## Notification widget (singleton, trigger-driven)

Tells the user when a text or email arrives.

- `event_sources: ["dbus"]` — subscribes to
  `org.freedesktop.Notifications` (and/or a mail/messaging source)
- On a matching event, calls `ctx.fire(payload={...})` with the sender /
  subject; the dashboard renders whatever response the user configured
  for this instance (badge, toast templating the payload, sound, etc.)
- Demonstrates: event sources, and the widget-fires / dashboard-responds
  split (see `docs/triggers.md`). The widget specifies no severity and no
  presentation.

## Trigger pop-up (any visibility, trigger-driven)

A widget that stays out of the way until a condition fires — e.g.
appears when a specific program launches.

- `visibility: "hidden_until_triggered"`
- `event_sources: ["process"]` — matches a program start
- On fire → calls `ctx.fire(payload={"program": "..."})`; the user's
  configured response for this instance does the revealing / toast /
  tab-switch
- Demonstrates: the full trigger → fire → dashboard-response chain; the
  marquee example of the trigger system

