# Code review — Widget Dashboard

Full read-through of the backend (`backend/widget_dashboard/`), the frontend
shell (`frontend/`), and the eight built-in widgets. Scope: correctness,
resource handling, and security, with the project's own framing in mind —
single-user, localhost-only, Linux/GNOME, widgets trusted at install time.

Section 1 lists the small, safe fixes. Section 2 covers the larger
design-level issues — **all now also fixed** in a follow-up pass. Section 3
records things that looked suspicious but are actually fine.

The test suite passes after the fixes (21 tests through the real
HTTP/WebSocket routes — the original 18 plus 3 new regression tests for the
input-safety guards).

---

## 1. Fixes applied in this review

### Backend

1. **`app.py` — path traversal on widget upload.** The multipart `file.filename`
   was joined straight into the staging dir, so a name like `../../…` could write
   outside it. Now reduced to its basename.

2. **`app.py` — arbitrary file delete via the `staged` parameter.** `install_widget`
   joined the client-supplied `staged` into the staging dir and `unlink()`ed it in
   a `finally`; a crafted value could delete a file elsewhere. Now reduced to its
   basename.

3. **`app.py` — widget asset path check.** The `/assets/{path}` guard used
   `str(f).startswith(str(assets_dir))`, which a sibling like `assets-evil` slips
   past. Switched to `Path.is_relative_to`.

4. **`profiles.py` — path traversal via tab/preset names.** Tab and preset names
   come from the UI and were used directly as `<name>.json` filenames, so `/`, `\`,
   `.`/`..`, or a NUL could read/overwrite/delete files outside the store. Added a
   central `_safe_name()` guard at every path-building site in `ProfileStore` and
   `PresetStore`.

5. **`app.py` — clean 400s for rejected names.** `create_tab`, `rename_tab`, and
   `save_preset` now translate the `ValueError` from #4 into a 400 instead of a 500
   (so a user typing a "/" in a tab name gets a clear error).

6. **`events.py` — timer "at HH:MM" crashes at month end.** The next-day rollover
   did `target.replace(day=now.day + 1)`, which raises on the 28th–31st. Replaced
   with `target += timedelta(days=1)`.

7. **`sysutil.py` — zombie after timeout.** The shared subprocess runner killed a
   timed-out child but never `wait()`ed it. Added `await proc.wait()` so it's
   reaped. (Benefits every caller: window/process/command event sources, host
   services, etc.)

### Built-in widgets

8. **`mixer/backend.py` — `_pactl` could hang forever.** It spawned `pactl` and
   `communicate()`d with no timeout, so a wedged PipeWire/pactl would block every
   snapshot and intent indefinitely (and never reap the child). Now routed through
   `sysutil.run`, which adds the timeout + kill + reap.

9. **`shell-command/backend.py` — timed-out command left running.** On the 30s
   capture timeout the subprocess wasn't killed, so it kept running detached and
   the next interval tick spawned another on top of it. Now `kill()` + `wait()` on
   timeout.

10. **`system-stats/backend.py` — `nvidia-smi` child not reaped.** `stop()`
    `terminate()`d the persistent `nvidia-smi` but didn't `wait()` it. Added the
    `await … .wait()`.

11. **`terminal/frontend.js` — markup injection via `innerHTML`.** The error path
    interpolated `msg.error` (which embeds the user's `shell`/`cwd` setting) into
    `innerHTML`, and the ready path interpolated `msg.url` into an iframe `src`
    string. Both now build elements and set `textContent` / `.src` as properties.

### Frontend shell (`frontend/shell.js`)

12. **Rejected module imports cached forever.** `loadWidgetModule` stored the
    `import()` promise even when it rejected, so one transient failure (or a widget
    with a syntax error, later fixed + rescanned) stayed broken for the life of the
    page. Now evicts the cache entry on failure.

13. **`openSettings` unhandled rejection.** A widget whose `settings.js` has a
    syntax error would throw an uncaught rejection and leave the drawer
    half-initialized. Wrapped the load in try/catch with a visible message.

14. **Install dialog XSS.** The permission/host-service/requires rows render an
    *unverified* package's manifest into `innerHTML` — the exact moment the user is
    deciding whether to trust it. Now escapes every interpolated field.

15. **Trigger overlay cut short.** `showOverlay` set a 2.5s hide timer without
    clearing the previous one, so a second trigger within 2.5s was hidden early by
    the first timer. Now clears the pending timer first.

---

## 2. Larger issues — now fixed (follow-up pass)

These needed design thought or touched more surface, so they were done as a
separate pass after Section 1. All are applied and covered by the test run.

- **A. Per-widget websockets never reconnected (`shell.js`, high) — fixed.**
  `makeWidgetApi` now manages a mutable socket with exponential backoff (1s →
  15s) that reconnects on a transient drop; handlers live in the closure so they
  survive a reconnect, and the backend replays current state on reattach. It
  stops reconnecting on close code `4404` (instance genuinely gone) and exposes a
  `close()` that cancels any pending timer; `unmountWidget` calls it.

- **B. Tab-switch race could leak sockets (`shell.js`, medium) — fixed.**
  `renderTab` now stamps a `renderGen` token and passes it to `mountWidget`,
  which bails after its `await import(...)` if a newer render has started —
  before opening a socket or mutating shared state. `renderTab` also re-checks
  the token after the parallel mount.

- **C. Unhandled API rejections / silent layout-save loss (`shell.js`, medium) —
  fixed.** All HTTP now goes through a single `request()` that checks `res.ok`,
  tolerates empty/non-JSON bodies, surfaces failures as a toast, and throws so
  callers don't proceed on bad data. The fire-and-forget layout save gets an
  explicit `.catch`.

- **D. `packaging.install` zip extraction (low / defense-in-depth) — fixed.**
  `validate()` now rejects any archive whose members use absolute paths, a drive
  letter, or `..`, with an explicit `unsafe path in archive` error — so a hostile
  `.wdwidget` is refused up front rather than relying on stdlib sanitization.
  (Regression test added.)

- **E. Fire-and-forget subprocesses/tasks (low) — fixed.** Added a `_reap`
  helper in `host_services` (retained-task set) used by `launch` and the
  clipboard writer; `events._dbus_loop` now `wait()`s the killed `dbus-monitor`;
  and `lockdown._watch_x` / `dashboard.layout_action` retain their
  `create_task(...)` handles so a pending task can't be GC'd.

- **F. mixer/terminal teardown hygiene (low–medium) — fixed.** The mixer's
  `stop()` now awaits the per-device `parec` reader tasks and `wait()`s their
  children (and the `pactl subscribe` proc); the terminal wraps spawn/kill in an
  `asyncio.Lock` so rapid restarts can't orphan a `ttyd`.

- **G. Endpoints assumed body keys (low) — fixed.** `add_instance`, `load_preset`,
  and `install_widget` validate their body fields and return 400; `update_layout`
  skips malformed position entries instead of `KeyError`-ing. (The name-guard
  from #4 in Section 1 also now returns a clean 400.)

---

## 3. Looked suspicious, actually fine

- **No shell injection in the subprocess paths.** `sysutil.run`,
  `host_services.launch`, and the `command`/`window`/`process` event loops all use
  `create_subprocess_exec` (+ `shlex.split` where a command string is parsed) — no
  shell.
- **`shell-command`'s `create_subprocess_shell` is by design** (it *is* the
  shell-command widget); `cwd` is validated and the capture path is time-boxed.
  Single-user, localhost, user's own setting.
- **`config.py` uses `yaml.safe_load`.**
- **Widget ids are validated** lowercase+hyphen, so the install target dir and the
  per-instance `state_dir` can't be steered by a malicious id; instance ids and
  per-widget colors are server-generated/validated.
- **Clipboard `xclip` is correctly left running and reaped** with a detached task
  (it must keep owning the X selection).

---

*Reviewed against the working tree; all fixes in Sections 1 and 2 are applied
and the test suite is green (21 tests).*
