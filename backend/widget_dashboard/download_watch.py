"""
"Install next download" watcher (docs/packaging.md 10.3).

Deliberately constrained: a permanent watcher would auto-grab any .wdwidget
ever dropped in Downloads (a drive-by risk). So this is single-shot and
time-boxed — it arms once, grabs the first matching file, then stops; or it
times out quietly. The grabbed file is MOVED into the widgets `incoming/`
staging dir and returned for the standard validate + permission-confirm flow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("download-watch")

WATCH_SECONDS = 120
POLL_INTERVAL = 1.0
PATTERN = "*.wdwidget"


def _download_dir() -> Path:
    """XDG_DOWNLOAD_DIR if set, else ~/Downloads (docs/packaging.md 10.3)."""
    env = os.environ.get("XDG_DOWNLOAD_DIR")
    if env:
        return Path(env)
    # user-dirs.dirs is the canonical GNOME source; fall back to ~/Downloads.
    dirs_file = Path.home() / ".config" / "user-dirs.dirs"
    if dirs_file.exists():
        for line in dirs_file.read_text().splitlines():
            if line.startswith("XDG_DOWNLOAD_DIR"):
                val = line.split("=", 1)[1].strip().strip('"')
                return Path(val.replace("$HOME", str(Path.home())))
    return Path.home() / "Downloads"


async def watch_for_widget(incoming_dir: Path,
                           timeout: float = WATCH_SECONDS) -> Path | None:
    """Watch the Downloads dir for the next new .wdwidget. Returns the staged
    path (moved into incoming_dir) or None on timeout."""
    downloads = _download_dir()
    if not downloads.exists():
        log.info("downloads dir %s missing; nothing to watch", downloads)
        return None

    seen = {p.name for p in downloads.glob(PATTERN)}
    deadline = time.time() + timeout
    log.info("watching %s for %s (%.0fs)", downloads, PATTERN, timeout)

    while time.time() < deadline:
        for p in sorted(downloads.glob(PATTERN)):
            if p.name in seen:
                continue
            # Wait briefly for the download to finish writing.
            await asyncio.sleep(0.5)
            staged = incoming_dir / p.name
            incoming_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(staged))
            log.info("grabbed %s → %s", p.name, staged)
            return staged
        await asyncio.sleep(POLL_INTERVAL)

    log.info("download watch timed out")
    return None
