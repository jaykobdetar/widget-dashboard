# Monitor lockdown

The dashboard monitor (monitor 2) should hold only the dashboard. Any
other window that opens there gets moved off.

## Mechanism

A small Python daemon, separate from the backend process, watches the X
root window for changes to `_NET_CLIENT_LIST`. When a new window appears:

1. Read its geometry (`_NET_WM_DESKTOP`, `_NET_FRAME_EXTENTS`, geometry).
2. Compute which monitor it primarily occupies (largest area overlap with
   each `xrandr` monitor rect).
3. If that monitor is the dashboard monitor *and* the window isn't in
   the allowlist, move it to the configured "fallback monitor" (default:
   monitor 1) using `wmctrl -i -r <id> -e 0,X,Y,W,H`.
4. Emit an event over the UNIX socket so the dashboard can show a toast.

## Allowlist

Living in `config.yaml`:

```yaml
lockdown:
  enabled: true
  dashboard_monitor: 2
  fallback_monitor: 1
  allowlist:
    by_pid: []                # populated at runtime with dashboard's chromium PID
    by_wm_class:              # static rules
      - "widget-dashboard"
    by_window_role: []
```

The dashboard's own Chromium PID is added to `by_pid` at launch so the
daemon never tries to bounce the dashboard itself. WM_CLASS rules let
the user pin specific apps (e.g. a media player) to monitor 2 if they
want exceptions.

## Failure modes

- **Window moves itself back immediately.** Some apps remember position.
  Mitigation: bounce-detection — if the same window-id triggers a move
  three times within 10 seconds, stop moving it and log a warning.
- **Fullscreen exclusive windows.** Games etc. may grab monitor 2
  regardless. Mitigation: detect `_NET_WM_STATE_FULLSCREEN` and either
  un-fullscreen or warn the user — exact behavior TBD (see open
  questions).
- **Window opens between poll and react.** With xlib event subscription
  the window we move was already visible for a few milliseconds. That's
  acceptable; this isn't a security boundary, it's a tidiness feature.

## Why a separate process

- Restart-independent from the dashboard UI. Dashboard can crash and
  reload; lockdown keeps running.
- Tight event loop just for X events, no HTTP server noise.
- Easier to disable (stop the systemd unit) without killing the
  dashboard.

## Disable / pause

- A "pause lockdown" button in the dashboard UI sends a message over
  the UNIX socket telling the daemon to ignore events for N minutes.
  Useful when intentionally placing something on monitor 2.
- Disabling lockdown in `config.yaml` and restarting the daemon turns
  it off entirely.
