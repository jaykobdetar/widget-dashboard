"""
Monitor lockdown (docs/lockdown.md).

A separate process watches the X root for new windows and, if a new window's
primary monitor is the dashboard monitor and its WM_CLASS isn't allowlisted,
moves it to the configured fallback monitor. This file contains two things:

- `LockdownDaemon` / `main()`: the standalone guard process. Run as
  `python -m widget_dashboard.lockdown`. Subscribes to _NET_CLIENT_LIST changes
  via python-xlib, bounces stray windows, and exposes a UNIX socket so the
  dashboard can pause it and receive bounce notifications.

- `LockdownClient`: the dashboard's side. Connects to that socket, forwards
  bounce events to the shell, and can pause the guard or allowlist a window
  the dashboard is about to launch onto its own monitor.

Both degrade quietly when their counterpart is absent: the dashboard runs fine
with no daemon (the client just no-ops), and the daemon prints a clear message
if python-xlib isn't installed rather than crashing.

Bounce-detection (docs/lockdown.md): if the same window-id is moved three times
in 10s we stop fighting it — some apps remember their own position.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from .config import Config
from .paths import LOCKDOWN_SOCKET

log = logging.getLogger("lockdown")


# ---------------------------------------------------------------------------
# Dashboard side: a thin client to the daemon's socket.
# ---------------------------------------------------------------------------

class LockdownClient:
    def __init__(self, config: Config,
                 on_event: Callable[[dict], Awaitable[None]]) -> None:
        self._config = config
        self._on_event = on_event
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def connect(self) -> None:
        """Try once to connect to a running daemon. Absence is normal (the
        daemon is an independent service) so failure is logged, not raised."""
        if not LOCKDOWN_SOCKET.exists():
            log.info("lockdown daemon socket absent; running without guard")
            return
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                str(LOCKDOWN_SOCKET)
            )
        except OSError as e:
            log.info("could not reach lockdown daemon: %s", e)
            return
        self._task = asyncio.create_task(self._read_loop())
        log.info("connected to lockdown daemon")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("lockdown read loop failed")
        finally:
            self._writer = None

    async def _send(self, msg: dict) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write((json.dumps(msg) + "\n").encode())
            await self._writer.drain()
        except Exception:
            self._writer = None

    async def pause(self, minutes: int) -> None:
        await self._send({"cmd": "pause", "minutes": minutes})

    async def resume(self) -> None:
        await self._send({"cmd": "resume"})

    async def allowlist_pid_window(self) -> None:
        """Briefly allowlist the next window so a deliberate launch onto the
        dashboard monitor isn't bounced (docs/host-services.md 5.1)."""
        await self._send({"cmd": "allowlist_next"})

    async def status(self) -> None:
        await self._send({"cmd": "status"})

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None


# ---------------------------------------------------------------------------
# Daemon side: the standalone X window-guard.
# ---------------------------------------------------------------------------

class LockdownDaemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._clients: set[asyncio.StreamWriter] = set()
        self._paused_until = 0.0
        self._allowlist_next = False
        # window-id -> [timestamps of recent bounces], for the give-up rule
        self._bounces: dict[str, list[float]] = {}

    # --- socket server (commands in, events out) ---

    async def _serve(self) -> None:
        LOCKDOWN_SOCKET.parent.mkdir(parents=True, exist_ok=True)
        if LOCKDOWN_SOCKET.exists():
            LOCKDOWN_SOCKET.unlink()
        server = await asyncio.start_unix_server(
            self._handle_client, str(LOCKDOWN_SOCKET)
        )
        log.info("lockdown socket listening at %s", LOCKDOWN_SOCKET)
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    cmd = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._handle_command(cmd, writer)
        finally:
            self._clients.discard(writer)

    async def _handle_command(self, cmd: dict, writer: asyncio.StreamWriter) -> None:
        name = cmd.get("cmd")
        if name == "pause":
            self._paused_until = time.time() + 60 * float(cmd.get("minutes", 5))
            log.info("lockdown paused for %s min", cmd.get("minutes"))
        elif name == "resume":
            self._paused_until = 0.0
        elif name == "allowlist_next":
            self._allowlist_next = True
        elif name == "status":
            await self._reply(writer, {
                "event": "status",
                "paused": time.time() < self._paused_until,
                "enabled": bool(self.config.get("lockdown_enabled", True)),
            })

    async def _reply(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        try:
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
        except Exception:
            self._clients.discard(writer)

    async def _emit(self, msg: dict) -> None:
        for writer in list(self._clients):
            await self._reply(writer, msg)

    # --- X watching ---

    @property
    def _paused(self) -> bool:
        return time.time() < self._paused_until

    def _should_bounce(self, wm_class: str) -> bool:
        allow = [c.lower() for c in self.config.get("lockdown_allowlist", [])]
        return wm_class.lower() not in allow

    def _record_bounce(self, wid: str) -> bool:
        """Return True if we should still bounce, False if we've given up
        (moved this window 3+ times in the last 10s)."""
        now = time.time()
        hist = [t for t in self._bounces.get(wid, []) if now - t < 10]
        if len(hist) >= 3:
            self._bounces[wid] = hist
            return False
        hist.append(now)
        self._bounces[wid] = hist
        return True

    async def _watch_x(self) -> None:
        try:
            from Xlib import X, display
            from Xlib.error import XError
        except ImportError:
            log.error(
                "python-xlib not installed — the lockdown guard cannot watch "
                "X. Install it (pip install python-xlib) to enable lockdown. "
                "The socket server still runs so pause/status work."
            )
            # Idle forever so the socket server keeps serving.
            await asyncio.Event().wait()
            return

        from . import sysutil

        loop = asyncio.get_running_loop()
        d = display.Display()
        root = d.screen().root
        root.change_attributes(event_mask=X.SubstructureNotifyMask | X.PropertyChangeMask)
        d.sync()

        net_client_list = d.intern_atom("_NET_CLIENT_LIST")
        known: set[int] = set()

        def read_event() -> None:
            try:
                while d.pending_events():
                    d.next_event()
            except XError:
                return
            try:
                prop = root.get_full_property(net_client_list, X.AnyPropertyType)
            except XError:
                return
            current = set(prop.value) if prop else set()
            new = current - known
            known.clear()
            known.update(current)
            for win_id in new:
                asyncio.create_task(self._consider_window(d, win_id))

        loop.add_reader(d.fileno(), read_event)
        # Seed the known set so we don't bounce everything already open.
        prop = root.get_full_property(net_client_list, X.AnyPropertyType)
        if prop:
            known.update(prop.value)
        await asyncio.Event().wait()

    async def _consider_window(self, d, win_id: int) -> None:
        from Xlib.error import XError
        from . import sysutil

        if self._paused or not self.config.get("lockdown_enabled", True):
            return
        if self._allowlist_next:
            self._allowlist_next = False
            return
        try:
            win = d.create_resource_object("window", win_id)
            cls = win.get_wm_class()
            wm_class = cls[1] if cls else ""
            geom = win.get_geometry()
            # translate_coords(root, 0, 0) maps the root origin into the
            # window's space, i.e. the negative of the window's absolute
            # position — negate to get true screen coordinates.
            rel = win.translate_coords(d.screen().root, 0, 0)
            x, y = -rel.x, -rel.y
            w, h = geom.width, geom.height
        except XError:
            return

        if not self._should_bounce(wm_class):
            return

        mons = await sysutil.monitors()
        on = sysutil.monitor_of_rect(mons, x, y, w, h)
        dash = self.config.get("dashboard_monitor", 0)
        if on != dash:
            return

        wid_s = str(win_id)
        if not self._record_bounce(wid_s):
            log.info("giving up on %s (moved too many times)", wm_class)
            return

        fallback = self.config.get("fallback_monitor", 1)
        target = next((m for m in mons if m.index == fallback), None)
        if target is not None:
            await sysutil.run("wmctrl", "-i", "-r", wid_s,
                              "-e", f"0,{target.x + 40},{target.y + 40},-1,-1")
        await self._emit({"event": "bounce", "wm_class": wm_class, "id": wid_s})
        log.info("bounced %s off dashboard monitor", wm_class)

    async def run(self) -> None:
        await asyncio.gather(self._serve(), self._watch_x())


def main() -> None:
    from .paths import CONFIG_DIR
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = Config(CONFIG_DIR / "config.yaml")
    daemon = LockdownDaemon(config)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
