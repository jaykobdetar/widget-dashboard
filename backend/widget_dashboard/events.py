"""
Event sources for triggers (docs/triggers.md).

> The widget owns the trigger. The dashboard owns the response.

A widget subscribes to a system event source and reacts in its handler —
typically by calling `ctx.fire(...)`. This module implements the six sources
named in the spec:

- timer   : interval or clock-time ticks
- process : a process matching a pattern starts / stops
- window  : a window with a given WM_CLASS / title appears / closes / focuses
- file    : a watched path changes
- command : a command run on an interval whose output changes or matches
- dbus    : a D-Bus signal (best-effort via `dbus-monitor`)

Each subscription owns one asyncio task. Tasks are tracked per instance and
cancelled when the instance stops, so nothing leaks across tab switches.
Sources poll where a kernel notification isn't trivially portable; intervals
are deliberately modest to stay cheap on a panel left running for hours.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from . import sysutil

log = logging.getLogger("events")

Handler = Callable[[dict], Awaitable[None]]


async def _timer_loop(match: dict, handler: Handler) -> None:
    interval = match.get("interval")
    at = match.get("at")  # "HH:MM"
    if at:
        while True:
            now = datetime.now()
            target = now.replace(
                hour=int(at[:2]), minute=int(at[3:5]), second=0, microsecond=0
            )
            if target <= now:
                target = target.replace(day=now.day + 1)
            await asyncio.sleep(max(1, (target - now).total_seconds()))
            await handler({"now": datetime.now().isoformat()})
    else:
        period = float(interval or 60)
        while True:
            await asyncio.sleep(period)
            await handler({"now": datetime.now().isoformat()})


async def _process_loop(match: dict, handler: Handler) -> None:
    pattern = re.compile(match.get("pattern", ".*"))
    want = match.get("event", "start")  # start | stop | any
    period = float(match.get("interval", 3))
    prev: set[str] = set()
    first = True
    while True:
        res = await sysutil.run("ps", "-eo", "pid=,comm=")
        current: set[str] = set()
        names: dict[str, str] = {}
        if res.ok:
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                pid, _, comm = line.partition(" ")
                if pattern.search(comm.strip()):
                    current.add(pid)
                    names[pid] = comm.strip()
        if not first:
            started = current - prev
            stopped = prev - current
            if want in ("start", "any"):
                for pid in started:
                    await handler({"event": "start", "pid": pid, "name": names.get(pid, "")})
            if want in ("stop", "any"):
                for pid in stopped:
                    await handler({"event": "stop", "pid": pid})
        prev = current
        first = False
        await asyncio.sleep(period)


async def _window_loop(match: dict, handler: Handler) -> None:
    wm_class = match.get("wm_class", "").lower()
    title_re = re.compile(match.get("title", ".*"))
    want = match.get("event", "appear")  # appear | close | focus
    period = float(match.get("interval", 2))
    prev: set[str] = set()
    prev_focus: str | None = None
    first = True
    while True:
        try:
            res = await sysutil.run("wmctrl", "-lx")
        except FileNotFoundError:
            return
        current: dict[str, str] = {}
        if res.ok:
            for line in res.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                wid, _desk, cls, _host, title = parts
                if wm_class and wm_class not in cls.lower():
                    continue
                if not title_re.search(title):
                    continue
                current[wid] = title
        if not first:
            if want == "appear":
                for wid in set(current) - prev:
                    await handler({"event": "appear", "id": wid, "title": current[wid]})
            elif want == "close":
                for wid in prev - set(current):
                    await handler({"event": "close", "id": wid})
        prev = set(current)
        first = False
        await asyncio.sleep(period)


async def _file_loop(match: dict, handler: Handler) -> None:
    path = Path(match["path"]).expanduser()
    period = float(match.get("interval", 2))
    prev: tuple | None = None
    while True:
        try:
            st = path.stat()
            sig = (st.st_mtime, st.st_size)
        except FileNotFoundError:
            sig = None
        if prev is not None and sig != prev:
            await handler({"path": str(path), "exists": sig is not None})
        prev = sig
        await asyncio.sleep(period)


async def _command_loop(match: dict, handler: Handler) -> None:
    cmd = match["command"]
    period = float(match.get("interval", 30))
    on = match.get("on", "change")  # change | match
    regex = re.compile(match["regex"]) if match.get("regex") else None
    prev_out: str | None = None
    import shlex
    parts = shlex.split(cmd)
    while True:
        try:
            res = await sysutil.run(*parts, timeout=period)
            out = res.stdout.strip()
        except FileNotFoundError:
            out = ""
        if on == "change":
            if prev_out is not None and out != prev_out:
                await handler({"output": out, "previous": prev_out})
        elif on == "match" and regex is not None:
            m = regex.search(out)
            if m:
                await handler({"output": out, "groups": list(m.groups())})
        prev_out = out
        await asyncio.sleep(period)


async def _dbus_loop(match: dict, handler: Handler) -> None:
    """Best-effort D-Bus signal watch via `dbus-monitor`. Parses signal
    headers loosely; emits the raw block as payload. Used by the notification
    widget to watch org.freedesktop.Notifications."""
    iface = match.get("interface", "")
    member = match.get("member", "")
    bus = match.get("bus", "session")
    msg_type = match.get("type", "signal")  # signal | method_call
    rule = f"type='{msg_type}'"
    if iface:
        rule += f",interface='{iface}'"
    if member:
        rule += f",member='{member}'"
    headers = ("signal ", "method call ")
    try:
        proc = await asyncio.create_subprocess_exec(
            "dbus-monitor", f"--{bus}", rule,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.warning("dbus-monitor not installed; dbus source inert")
        return
    try:
        block: list[str] = []
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line.startswith(headers) and block:
                await handler({"raw": "\n".join(block)})
                block = [line]
            else:
                block.append(line)
    finally:
        proc.kill()


_LOOPS: dict[str, Callable[[dict, Handler], Awaitable[None]]] = {
    "timer": _timer_loop,
    "process": _process_loop,
    "window": _window_loop,
    "file": _file_loop,
    "command": _command_loop,
    "dbus": _dbus_loop,
}


class EventBus:
    """Per-instance subscription manager. A widget may only subscribe to the
    sources it declared in `event_sources`; everything else is refused."""

    def __init__(self, declared: list[str]) -> None:
        self._declared = set(declared)
        self._tasks: list[asyncio.Task] = []

    async def subscribe(self, source: str, match: dict, handler: Handler) -> None:
        if source not in _LOOPS:
            raise ValueError(f"unknown event source {source!r}")
        if source not in self._declared:
            raise PermissionError(
                f"widget did not declare event_source {source!r} in its manifest"
            )

        async def guarded() -> None:
            try:
                await _LOOPS[source](match, handler)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event source %s crashed", source)

        self._tasks.append(asyncio.create_task(guarded()))

    async def cancel_all(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._tasks.clear()
