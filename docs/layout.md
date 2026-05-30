# Layout

The dashboard hosts widget instances on a grid. Layout is a runtime user
concern — the user decides what to place, where, and at what size.

## Grid

- 12-column grid spanning the full dashboard monitor
- Row height fixed (probably 60px, TBD)
- Widgets declare a `default_size` and `min_size` in their manifest
- Drag to move, drag corners to resize
- Library: `gridstack.js` (mature, framework-free, exactly this use case)

## Editing (always on)

There is no separate "edit mode" — the grid is always editable:

- Drag a widget by its **grip** (revealed on hover) to move it; drag edges to
  resize. Dragging only from the grip means a widget's own controls (sliders,
  rows) stay interactive.
- Hovering a widget reveals its chrome: **⚙ settings**, **⚡ trigger response**,
  and **✕ remove**.
- A floating **+** button (bottom-right) opens the widget picker; an empty tab
  also shows an "Add a widget" prompt.
- The `⋯` menu (top-right) holds install / rescan / pause-lockdown.

Layout changes are held in memory and persisted only on an explicit **Save**
(see below) — not automatically.

## Tabs and profiles

A **profile** is a named widget layout. Profiles are surfaced as a
**tab bar** along one edge of the dashboard (top by default): each tab
is a profile, and clicking a tab switches which layout is on screen.
Tabs and profiles are the same thing — "tab" is the UI, "profile" is the
stored object. There is no separate dropdown; the tab bar *is* the
profile switcher.

This makes switching between arrangements a single click — a "Work" tab
with mixer + window list + system stats, a "Music" tab with a big
now-playing + EQ, an "Idle" tab with a clock + weather. Each tab is a
full, independent layout.

Profiles live in `~/.config/widget-dashboard/profiles/<name>.json`. The tab
bar shows one tab per profile plus:

- A `+` to create a new tab (empty grid, prompts for a name)
- Right-click a tab → rename, duplicate, delete, reorder
- The selected tab is highlighted
- Each tab has an enable/disable toggle (see below)

### Tab states: disabled / enabled / selected

A tab is in one of three states:

- **Disabled** — not running. Its widgets' backends are stopped; no
  polling, no event subscriptions, no triggers, no notifications. Costs
  nothing. This is the default.
- **Enabled** — running in the background but not rendered. Its widgets'
  backends run, their triggers fire, and they can notify you (per each
  instance's response config). Use this for a tab you're not looking at
  but want watching — e.g. a notifications tab that alerts you when mail
  arrives so you can switch to it and check.
- **Selected** — running *and* rendered. Exactly one tab is selected at
  a time; it's the one on screen.

Rules:

- **The selected tab is always running.** You can't view a tab that
  isn't running, so selecting a tab forces it on.
- **Selecting a disabled tab is a temporary view-time override, not a
  state change.** When you switch away, the tab reverts to disabled. So
  peeking at a disabled tab does not silently leave it running in the
  background — it goes dormant again the moment you leave.
- **An enabled tab stays enabled when you switch away.** Enabling is the
  explicit "keep this watching" action; selecting is not.
- **On launch, every tab starts disabled.** Nothing runs in the
  background until you deliberately enable it this session. (The tab that
  becomes selected on launch runs because it's selected, not because of
  any saved enabled state.)

The property that falls out: a tab only keeps running in the background
if you *explicitly enabled* it (not merely looked at it), and that intent
resets on every restart. Background resource use and background
notifications are therefore always something opted into during the
current session — never a surprise, which matters for a system that can
spawn helper processes and hold D-Bus subscriptions.

Enabled/disabled state is **not persisted** across restarts (always
starts disabled). It is held in memory only. Selecting, enabling, and
disabling are runtime actions, not saved properties of the profile file.

### Explicit save / load / revert + presets

Persistence is explicit (a document model), not auto-save:

- Edits (move/resize, add/remove, settings, trigger response) are applied live
  but held **in memory**; the tab shows an **unsaved-changes dot** until you
  **Save**.
- The tab toolbar has **Save** (write this tab to disk), **Load ▾** (apply a
  saved preset into this tab — see below), and **Revert** (discard unsaved
  changes, reloading the tab from disk).
- Switching tabs does **not** auto-save, but in-memory edits are kept while the
  app runs — only **Revert**, **Load**, or quitting without saving discards
  them.
- **Presets** are a reusable layout library, separate from the tab bar:
  "Save as preset…" snapshots the current tab under a name; "Load ▾" applies any
  preset into the current tab (with fresh instance ids). Stored in
  `~/.config/widget-dashboard/presets/<name>.json`.

(This document model replaces the original auto-save design at the user's
request.) For a throwaway arrangement, edit freely and just **Revert** instead
of saving, or duplicate the tab first.

### What persists where

Tab order is stored in `~/.config/widget-dashboard/profiles/_order.json` (a JSON
list of profile names). `config.yaml` holds global settings (dashboard monitor,
lockdown allowlist, the default trigger-response template) — not per-tab state.

Each profile file is a self-contained layout (note each instance also carries a
`response` block for its trigger config):

`~/.config/widget-dashboard/profiles/work.json`:

```json
{
  "version": 1,
  "name": "work",
  "grid": {
    "columns": 12,
    "row_height": 60
  },
  "instances": [
    {
      "id": "inst_a1b2",
      "widget_id": "mixer",
      "x": 0, "y": 0, "w": 6, "h": 4,
      "settings": {}
    },
    {
      "id": "inst_c3d4",
      "widget_id": "shell-command",
      "x": 6, "y": 0, "w": 3, "h": 2,
      "settings": {
        "command": "uptime",
        "interval": 5,
        "display": "text"
      }
    }
  ]
}
```

Settings for each instance live inside the profile file, not in a
separate per-widget file. Reasoning: layout and settings are equally
"this user's arrangement of this tab"; keeping them together makes a
profile a single-file snapshot.

Per-instance *state* (graph buffers, history) lives separately, in
`~/.local/state/widget-dashboard/widget-state/<instance-id>/`, because
state is large and disposable. Switching tabs does not preserve the
previous tab's live state — returning to a tab gives fresh instances.

### Trigger-driven widgets across tabs

The trigger system (see `docs/triggers.md`) raises the question: if a
"pop up when program X launches" widget lives only on the Music tab, can
it fire while you're looking at the Work tab?

This is resolved by tab states (above): **enable** the Music tab.
Enabled tabs run in the background, so their widgets' triggers fire and
can notify you (including switching you to that tab, if its response is
configured to) even while another tab is selected. A disabled tab's
triggers don't fire.

This replaces an earlier proposal for an author-declared
`background: true` widget flag. Making it a user-controlled per-tab
toggle is more flexible — the user decides what watches in the
background, with no involvement from the widget author, and the decision
resets each session.
