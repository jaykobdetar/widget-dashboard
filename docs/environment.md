# Environment

## Target

- Ubuntu (current LTS)
- GNOME desktop, **X11 session** ("GNOME on Xorg" at login)
- PipeWire audio (with PulseAudio compatibility layer)
- Three monitors: monitor 1 and monitor 3 are normal work surfaces; monitor 2
  is the dedicated dashboard monitor.

## Why X11 and not Wayland

Wayland-on-GNOME blocks the protocols this project depends on:

- **wlr-layer-shell** (used by Eww, AGS, Waybar, etc.) is not implemented in
  Mutter and won't be. This rules out the "modern" widget toolkits.
- **Window introspection** of other applications' windows is restricted on
  Wayland by design. Listing windows on other monitors requires a GNOME Shell
  extension running inside Mutter, which means writing the window-inventory
  and lockdown features in GJS against an API that breaks every GNOME release.
- **Moving windows between monitors** programmatically has no public Wayland
  protocol; again, extension-only.

X11 has none of these problems. `wmctrl`, `xdotool`, and `python-xlib` give
direct, stable access to the window list and geometry, and moving windows
between monitors is one shell command.

The cost is being on X11. For a desktop widget-dashboard project that is an
acceptable trade. The session switch is per-login (gear icon → "GNOME on
Xorg").

## Required system packages

- `ttyd` — embedded terminal
- `wmctrl`, `xdotool` — window queries and manipulation
- `wireplumber` (for `wpctl`), `pulseaudio-utils` (for `pactl` fallback)
- `python3-venv`
- `chromium-browser` or `google-chrome` — for `--app` mode

Python deps (in a venv) will include `fastapi`, `uvicorn`, `websockets`,
`python-xlib`, `pyyaml`, `watchfiles`.
