# Packaging & installing widgets

Widgets are distributed as `.wdwidget` files and installed either by dropping
them into the widgets directory or via the "install next download" flow.

## The `.wdwidget` format

A `.wdwidget` file is a ZIP archive with the widget's files **at the root**
(not nested in a folder):

```
disk-free.wdwidget
  ├── widget.json
  ├── backend.py
  ├── frontend.js
  ├── style.css
  └── settings.js        (if present)
```

The archive is named `<id>.wdwidget` where `<id>` matches `widget.json`'s
`id`. A distinct extension (rather than bare `.zip`) is deliberate: it lets
the download watcher recognise widgets unambiguously and avoids grabbing
unrelated zips.

## Validation on install

Before a `.wdwidget` is accepted, the dashboard:

1. Confirms it's a valid zip.
2. Confirms `widget.json` exists at the root and parses.
3. Confirms `backend.py` and `frontend.js` exist.
4. Confirms `id` is well-formed (lowercase, hyphenated) and doesn't collide
   with an installed widget (unless this is an explicit update — same `id`,
   higher `version`).
5. Extracts to a temp dir and reads the manifest's `permissions`,
   `host_services`, and `requires`.
6. **Presents that to the user and waits for confirmation.** Installing code
   that runs in the backend process is a trust decision; the user sees what
   the widget wants before it's activated.

Only after confirmation is the folder moved into
`~/.local/share/widget-dashboard/widgets/<id>/` and the registry rescanned.

Static validation does not, and cannot, prove the code is safe — it only
surfaces declared intent. This is the same trust model as the rest of the
project (docs/widgets.md "Sandbox"): widgets are user/AI-authored, declared
permissions aid review, and a future subprocess sandbox is the real
enforcement. The confirm step makes the trust decision explicit rather than
implicit.

## Manual install

Drop a `.wdwidget` into the widgets directory's `incoming/` subfolder, or
extract it directly into `~/.local/share/widget-dashboard/widgets/<id>/`, then
click **rescan**. The dashboard also watches `incoming/` and runs the
validation+confirm flow on anything that lands there.

## "Install next download"

A one-click flow for the common case of downloading a widget from a chat or
the web:

1. The user clicks **Install next download** in the dashboard.
2. The dashboard begins watching the user's Downloads directory (resolved via
   `XDG_DOWNLOAD_DIR`, falling back to `~/Downloads`) for **new** files
   matching `*.wdwidget`.
3. The watch is **time-boxed** (default 2 minutes) and **single-shot**: it
   grabs the first matching file that appears after the click, then stops
   watching. It never becomes a permanent background watcher of the
   Downloads folder.
4. When a match appears, the file is moved (not copied) into the widgets
   `incoming/` area and the standard validation + permission-confirm flow
   runs.
5. On confirm, install completes and the picker refreshes. On timeout with no
   match, the watch quietly ends and the UI says so.

### Why these constraints

- **Single-shot + time-boxed**: the user expressed intent ("I am about to
  download a widget"), so the watch is scoped to that intent. A permanent
  watcher that auto-installs anything ever dropped in Downloads would be a
  real security problem — any drive-by `.wdwidget` would self-install.
- **Extension filter**: only `*.wdwidget`, so ordinary downloads are ignored.
- **Move, not copy**: leaves Downloads clean and makes the hand-off obvious.
- **Confirm step still applies**: "install next download" lands and validates
  the file but never activates code without the permission-confirm click.
  Convenience does not bypass the trust gate.

### Watcher implementation note

Use inotify (via the backend, e.g. `watchfiles` or `inotify_simple`) scoped
to the single Downloads directory, started on the button click and torn down
on match or timeout. Do not poll, and do not keep the watch alive across the
button's lifetime.

## Updating a widget

Installing a `.wdwidget` whose `id` matches an installed widget and whose
`version` is higher is treated as an update: the folder is replaced and, per
docs/open-questions.md, instances are reloaded on the next rescan (or
immediately if the user chooses "reload now"). Downgrades and same-version
reinstalls prompt for confirmation.

## Exporting

The dashboard can also produce a `.wdwidget` from an installed widget (zip
the folder contents at the root), so a widget authored or tweaked locally can
be shared or backed up.
