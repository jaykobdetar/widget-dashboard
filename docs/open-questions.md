# Open questions

Things that need a decision before or during implementation.

## CSS scoping for widget styles

Two options for keeping a widget's `style.css` from leaking into the
shell or other widgets:

1. **Shadow DOM**: each widget mounts into a shadow root. Strong
   isolation, but breaks some CSS niceties (no global theme variables
   inherited unless explicitly passed in) and complicates third-party
   libraries that query the DOM.
2. **Rewriting selectors at load time**: prefix every selector with
   `[data-widget-instance="<id>"]`. Simpler integration, weaker
   isolation.

Leaning toward shadow DOM with a small set of CSS custom properties
explicitly forwarded for theming (`--bg`, `--fg`, `--accent`, etc.) but
not decided.

## Theming

The shell should have a theme (dark by default, probably light too).
Widgets should pick up the theme via CSS custom properties. Need to
nail down the full set of forwarded properties before widget authoring
begins, since AI-written widgets need to know what to consume.

## Per-widget subprocess sandbox

v1 runs widget backends in the main backend process. Future v2 should
spawn each widget backend as a subprocess and apply seccomp / landlock
filters matching its declared `permissions`. Worth designing the plugin
API now so this is a drop-in change later — specifically, the `ctx`
object should already act like an RPC surface, not direct in-process
calls.

## Fullscreen-grabbing applications and lockdown

If a game or media player goes exclusive-fullscreen on monitor 2, what
should the lockdown daemon do?

- Option A: yank it to the fallback monitor like anything else (likely
  breaks the app)
- Option B: detect fullscreen, un-fullscreen it, then move it
- Option C: log and ignore (let the user reach for the "pause lockdown"
  button)

Probably C with a notification, but worth confirming.

## Undo for layout edits

Nice-to-have. A small ring buffer of the last N `layout.json` states,
with a Ctrl+Z keybinding in edit mode. Low priority; not v1.

## Widget update / versioning

When the user replaces a widget folder with a newer version, instances
of the old version are still running. Strategy:

1. Reload all instances of the changed widget (visible flicker)
2. Reload only on next dashboard restart (changes invisible until then)
3. Reload only if the manifest's `version` changed

Probably (3), with the rescan button also offering "reload instances
of changed widgets."

## Inter-widget communication

Should widgets be able to talk to each other? Example: a "now playing"
widget telling a "lyrics" widget what song is current.

Tentative answer: no in v1. Widgets are independent. If two widgets
need shared state, they should both subscribe to the same system source
(MPRIS, in the example). Keeps the plugin contract small.

## Multi-monitor scaling

If the dashboard monitor has a different DPI than the others, the
Chromium window respects its own monitor's scale; this should Just
Work. Worth testing on a HiDPI + normal-DPI mixed setup before
declaring it done.
