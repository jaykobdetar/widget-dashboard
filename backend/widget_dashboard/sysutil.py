"""
Small shared helpers for talking to the system: async subprocess running and
xrandr monitor geometry. Kept separate so host services, the lockdown daemon,
and widgets that legitimately need geometry all share one implementation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

log = logging.getLogger("sysutil")


@dataclass
class CommandResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


async def run(*args: str, input_bytes: bytes | None = None,
              timeout: float = 10.0) -> CommandResult:
    """Run a command, capturing output. Never raises on non-zero exit — the
    caller inspects `.ok`. Raises only if the binary is missing."""
    if shutil.which(args[0]) is None:
        raise FileNotFoundError(args[0])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=input_bytes), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()   # reap the killed child so it doesn't linger as a zombie
        return CommandResult(124, "", "timeout")
    return CommandResult(
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


@dataclass
class Monitor:
    index: int
    name: str
    x: int
    y: int
    w: int
    h: int
    primary: bool = False

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


_GEOM = re.compile(r"(\d+)x(\d+)\+(\d+)\+(\d+)")

# Monitor geometry barely ever changes, but it was being re-queried on every
# window-inventory snapshot (spawning xrandr ~1/s). Cache it with a short TTL.
_MON_CACHE: list["Monitor"] | None = None
_MON_CACHE_AT = 0.0
_MON_TTL = 10.0


async def monitors(force: bool = False) -> list[Monitor]:
    """Connected outputs from `xrandr`, in xrandr order (0-based index).

    Cached for a few seconds (geometry rarely changes); pass force=True to
    bypass. Returns [] if xrandr is unavailable (e.g. headless CI), so callers
    must cope with an empty list rather than assuming a monitor exists.
    """
    global _MON_CACHE, _MON_CACHE_AT
    import time as _time
    if not force and _MON_CACHE is not None and (_time.monotonic() - _MON_CACHE_AT) < _MON_TTL:
        return _MON_CACHE
    try:
        res = await run("xrandr", "--query")
    except FileNotFoundError:
        _MON_CACHE, _MON_CACHE_AT = [], _time.monotonic()
        return []
    if not res.ok:
        return _MON_CACHE or []
    out: list[Monitor] = []
    idx = 0
    for line in res.stdout.splitlines():
        if " connected" not in line:
            continue
        m = _GEOM.search(line)
        if not m:
            continue
        w, h, x, y = (int(g) for g in m.groups())
        name = line.split()[0]
        out.append(Monitor(idx, name, x, y, w, h, primary=" primary " in line))
        idx += 1
    _MON_CACHE, _MON_CACHE_AT = out, _time.monotonic()
    return out


def monitor_of_rect(mons: list[Monitor], x: int, y: int, w: int, h: int) -> int | None:
    """Which monitor index a window mostly lives on (by its center point)."""
    cx, cy = x + w // 2, y + h // 2
    for mon in mons:
        if mon.contains(cx, cy):
            return mon.index
    return None
