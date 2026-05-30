"""
Host services (docs/host-services.md) — shared dashboard capabilities widgets
call through `ctx.host.*`. A widget may only reach the services it declared in
`host_services`; the registry enforces that by handing each instance a
`HostServices` scoped to its declared set.

Privileged work (launching windows, touching the clipboard, native drag) lives
here rather than in widget code so that a future per-widget subprocess sandbox
(docs/open-questions.md) becomes a drop-in change: today these are in-process
calls, later they can become RPC across a process boundary with widget code
untouched.

Implementations shell out to wmctrl / xdotool / xclip. Each degrades to a
clear error rather than a crash when its tool is missing, so the dashboard
still runs in environments where a given capability is unavailable.
"""

from __future__ import annotations

import base64
import logging
import struct
import zlib
from typing import TYPE_CHECKING

from . import sysutil

if TYPE_CHECKING:
    from .dashboard import Dashboard
    from .instances import WidgetInstance

log = logging.getLogger("host")


def _as_text(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Minimal PNG encoder for 8-bit RGBA (no external deps)."""
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)                       # filter type 0 per scanline
        raw += rgba[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 9)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def _extract_icon(values) -> str | None:
    """Turn a _NET_WM_ICON CARD32 array ([w,h,ARGB pixels,...] possibly for
    several sizes) into a PNG data URL, choosing the size closest to 32px."""
    vals = list(values)
    n = len(vals)
    best = None   # (score, w, h, start)
    i = 0
    while i + 2 <= n:
        w, h = vals[i], vals[i + 1]
        i += 2
        if w <= 0 or h <= 0 or i + w * h > n:
            break
        score = abs(w - 32)
        if best is None or score < best[0]:
            best = (score, w, h, i)
        i += w * h
    if best is None:
        return None
    _, w, h, start = best
    if w * h > 64 * 64:                      # bound work for huge icons
        return None
    rgba = bytearray(w * h * 4)
    for j in range(w * h):
        p = vals[start + j] & 0xFFFFFFFF
        k = j * 4
        rgba[k] = (p >> 16) & 0xFF           # R
        rgba[k + 1] = (p >> 8) & 0xFF        # G
        rgba[k + 2] = p & 0xFF               # B
        rgba[k + 3] = (p >> 24) & 0xFF       # A
    png = _png_rgba(w, h, bytes(rgba))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class WindowsService:
    """Window introspection and placement (docs/host-services.md 5.1).

    Cooperates with the lockdown daemon: launching onto the dashboard monitor
    allowlists the new window for the launch, so the guard doesn't bounce it.
    """

    def __init__(self, dashboard: "Dashboard") -> None:
        self._dash = dashboard
        self._display = None            # cached Xlib display
        self._icon_cache: dict = {}     # window id -> (signature, data-url)

    def dashboard_monitor(self) -> int:
        """Which monitor index the dashboard occupies. Note: this is a config
        value, not auto-detected, so widgets that group by monitor should treat
        all monitors uniformly rather than relying on it."""
        return int(self._dash.config.get("dashboard_monitor", 0))

    async def list(self) -> list[dict]:
        """All real windows with the monitor each currently sits on, plus an
        icon. Uses python-xlib for ABSOLUTE geometry (accurate across monitors,
        unlike wmctrl -lG on mutter) and `_NET_WM_ICON`; falls back to wmctrl
        where X/xlib is unavailable."""
        try:
            return await self._list_xlib()
        except Exception as e:  # noqa: BLE001 — degrade to the wmctrl path
            log.debug("xlib window list failed (%s); using wmctrl", e)
            return await self._list_wmctrl()

    async def _list_xlib(self) -> list[dict]:
        from Xlib import X, Xatom  # noqa: F401

        d = self._get_display()
        root = d.screen().root
        atom = d.intern_atom
        a_clients = atom("_NET_CLIENT_LIST")
        a_name = atom("_NET_WM_NAME")
        a_type = atom("_NET_WM_WINDOW_TYPE")
        a_type_normal = atom("_NET_WM_WINDOW_TYPE_NORMAL")
        a_type_dialog = atom("_NET_WM_WINDOW_TYPE_DIALOG")
        a_type_util = atom("_NET_WM_WINDOW_TYPE_UTILITY")
        a_icon = atom("_NET_WM_ICON")
        a_utf8 = atom("UTF8_STRING")
        a_state = atom("_NET_WM_STATE")
        a_above = atom("_NET_WM_STATE_ABOVE")
        a_sticky = atom("_NET_WM_STATE_STICKY")
        a_fullscr = atom("_NET_WM_STATE_FULLSCREEN")
        keep_types = {a_type_normal, a_type_dialog, a_type_util}

        prop = root.get_full_property(a_clients, Xatom.WINDOW)
        ids = list(prop.value) if prop else []
        mons = await sysutil.monitors()
        live = set()
        out: list[dict] = []
        for wid in ids:
            try:
                win = d.create_resource_object("window", wid)
                tp = win.get_full_property(a_type, Xatom.ATOM)
                types = set(tp.value) if tp else set()
                # Skip docks/desktop/menus/etc.; keep normal/dialog/utility (or
                # untyped, which some apps leave blank).
                if types and not (types & keep_types):
                    continue
                geom = win.get_geometry()
                pos = win.translate_coords(root, 0, 0)
                # translate_coords returns the root-origin relative to the
                # window; the window's absolute top-left is its negation.
                x, y = -pos.x, -pos.y
                w, h = geom.width, geom.height

                name_prop = win.get_full_property(a_name, a_utf8)
                if name_prop and name_prop.value:
                    title = _as_text(name_prop.value)
                else:
                    wn = win.get_wm_name()
                    title = wn if isinstance(wn, str) else _as_text(wn or b"")
                cls = win.get_wm_class()
                wm_class = cls[1] if cls else ""

                st = win.get_full_property(a_state, Xatom.ATOM)
                states = set(st.value) if st else set()

                hexid = "0x%08x" % wid
                live.add(hexid)
                out.append({
                    "id": hexid,
                    "desktop": 0,
                    "title": title,
                    "wm_class": wm_class,
                    "monitor": sysutil.monitor_of_rect(mons, x, y, w, h),
                    "geometry": {"x": x, "y": y, "w": w, "h": h},
                    "icon": self._icon_for(hexid, win, a_icon),
                    "above": a_above in states,
                    "sticky": a_sticky in states,
                    "fullscreen": a_fullscr in states,
                })
            except Exception:  # noqa: BLE001 — window vanished mid-iteration
                continue
        # Drop cached icons for windows that no longer exist.
        for gone in [k for k in self._icon_cache if k not in live]:
            self._icon_cache.pop(gone, None)
        return out

    def _get_display(self):
        if self._display is None:
            from Xlib import display
            self._display = display.Display()
        return self._display

    def _icon_for(self, hexid, win, a_icon) -> str | None:
        from Xlib import Xatom
        try:
            prop = win.get_full_property(a_icon, Xatom.CARDINAL)
        except Exception:  # noqa: BLE001
            return None
        if not prop or not prop.value:
            return self._icon_cache.get(hexid, (None, None))[1]
        values = prop.value
        sig = (len(values), int(values[0]) if len(values) else 0)
        cached = self._icon_cache.get(hexid)
        if cached and cached[0] == sig:
            return cached[1]
        url = _extract_icon(values)
        self._icon_cache[hexid] = (sig, url)
        return url

    async def _list_wmctrl(self) -> list[dict]:
        """Fallback: join `wmctrl -lG` (geometry) with `wmctrl -lx` (class).
        No icons; geometry can be approximate on some WMs."""
        try:
            geo_res = await sysutil.run("wmctrl", "-lG")
            cls_res = await sysutil.run("wmctrl", "-lx")
        except FileNotFoundError:
            raise RuntimeError("wmctrl not installed")
        if not geo_res.ok:
            return []
        wm_class: dict[str, str] = {}
        for line in cls_res.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 3:
                wm_class[parts[0]] = parts[2].split(".")[-1]
        mons = await sysutil.monitors()
        windows = []
        for line in geo_res.stdout.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            wid, desktop, x, y, w, h, host, title = parts
            x, y, w, h = (int(v) for v in (x, y, w, h))
            windows.append({
                "id": wid, "desktop": int(desktop), "title": title,
                "wm_class": wm_class.get(wid, ""),
                "monitor": sysutil.monitor_of_rect(mons, x, y, w, h),
                "geometry": {"x": x, "y": y, "w": w, "h": h},
                "icon": None, "above": False, "sticky": False, "fullscreen": False,
            })
        return windows

    async def focus(self, window_id: str) -> None:
        await sysutil.run("wmctrl", "-i", "-a", window_id)

    async def close(self, window_id: str) -> None:
        await sysutil.run("wmctrl", "-i", "-c", window_id)

    async def _unmaximize(self, window_id: str) -> None:
        # Maximized/fullscreen windows ignore geometry moves; clear those first.
        await sysutil.run("wmctrl", "-i", "-r", window_id,
                          "-b", "remove,maximized_vert,maximized_horz")
        await sysutil.run("wmctrl", "-i", "-r", window_id,
                          "-b", "remove,fullscreen")

    async def move(self, window_id: str, monitor: int) -> None:
        """Move a window so its top-left sits on the given monitor."""
        mons = await sysutil.monitors()
        target = next((m for m in mons if m.index == monitor), None)
        if target is None:
            raise ValueError(f"no monitor with index {monitor}")
        await self._unmaximize(window_id)
        await sysutil.run(
            "wmctrl", "-i", "-r", window_id,
            "-e", f"0,{target.x + 40},{target.y + 40},-1,-1",
        )

    async def set_geometry(self, window_id: str, x: int, y: int, w: int, h: int) -> None:
        """Place a window at absolute root coordinates with a given size."""
        await self._unmaximize(window_id)
        await sysutil.run(
            "wmctrl", "-i", "-r", window_id, "-e",
            f"0,{int(x)},{int(y)},{int(w)},{int(h)}",
        )

    async def maximize(self, window_id: str) -> None:
        await sysutil.run("wmctrl", "-i", "-r", window_id,
                          "-b", "add,maximized_vert,maximized_horz")

    async def fullscreen(self, window_id: str) -> None:
        """Toggle fullscreen state."""
        await self.set_window_state(window_id, "fullscreen")

    async def set_window_state(self, window_id: str, state: str,
                               action: str = "toggle") -> None:
        """Add/remove/toggle an EWMH window state, e.g. 'above' (always on top),
        'sticky' (all workspaces), 'fullscreen'. action ∈ add|remove|toggle."""
        await sysutil.run("wmctrl", "-i", "-r", window_id,
                          "-b", f"{action},{state}")

    async def launch(self, command: str, monitor: int | None = None,
                     geometry: dict | None = None, workspace: int | None = None) -> None:
        """Spawn a command. If it should land on the dashboard monitor, ask
        lockdown to allowlist it briefly so it isn't immediately bounced."""
        if monitor is not None and monitor == self._dash.config.get("dashboard_monitor"):
            await self._dash.lockdown.allowlist_pid_window()
        import asyncio
        import shlex
        await asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )


class ClipboardService:
    """Clipboard writes via xclip (docs/host-services.md 5.2).

    Setting an X selection means *owning* it: `xclip` stays alive to serve the
    content until another app takes over. So we must NOT wait for it to exit
    (that would block forever) — we hand it the data and let it run in the
    background, reaping it with a detached task.
    """

    async def _feed_xclip(self, data: bytes, *extra: str) -> None:
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                "xclip", "-selection", "clipboard", *extra,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise RuntimeError("xclip not installed")
        assert proc.stdin is not None
        proc.stdin.write(data)
        proc.stdin.close()                  # EOF: xclip takes ownership now
        # Reap when it eventually loses ownership and exits, without blocking us.
        asyncio.create_task(proc.wait())

    async def set_text(self, text: str) -> None:
        await self._feed_xclip(text.encode())

    async def set_files(self, paths: list[str]) -> None:
        """Put real files on the clipboard so Ctrl-V in a file manager pastes
        the files themselves — uses the text/uri-list target Nautilus expects."""
        uris = "\n".join(f"file://{p}" for p in paths) + "\n"
        await self._feed_xclip(uris.encode(), "-t", "text/uri-list")


class DragService:
    """Native drag-out helper (docs/host-services.md 5.3).

    A web page in Chromium --app cannot initiate a native XDND drag
    (docs/constraints.md), so this would invoke a small bundled GTK helper.
    The helper is not shipped in this slice; the call is wired so the file
    manager widget's drag path exists and degrades to a clear message.
    """

    async def start_files(self, paths: list[str]) -> None:
        raise RuntimeError(
            "native drag helper not bundled in this build; use click-to-copy "
            "(ctx.host.clipboard.set_files) — see docs/constraints.md"
        )


class LayoutService:
    """A widget managing its OWN visibility (docs/host-services.md 5.4).

    NOT for grabbing attention — that always routes through ctx.fire and the
    response config. These send a control message to the shell, which adjusts
    this instance's grid node.
    """

    def __init__(self, instance: "WidgetInstance") -> None:
        self._inst = instance

    async def reveal(self) -> None:
        self._inst.dashboard.layout_action(self._inst.instance_id, "reveal")

    async def hide(self) -> None:
        self._inst.dashboard.layout_action(self._inst.instance_id, "hide")

    async def collapse(self) -> None:
        self._inst.dashboard.layout_action(self._inst.instance_id, "collapse")


# A single registry of which services exist and how to build one, scoped per
# instance. Widgets only get the ones they declared in `host_services`.
_BUILDERS = {
    "windows": lambda inst: WindowsService(inst.dashboard),
    "clipboard": lambda inst: ClipboardService(),
    "drag": lambda inst: DragService(),
    "layout": lambda inst: LayoutService(inst),
}


class HostServices:
    """Per-instance facade. Accessing a service the widget didn't declare is a
    clear error (not silent), keeping the manifest honest."""

    def __init__(self, instance: "WidgetInstance", declared: list[str]) -> None:
        self._declared = set(declared)
        self._cache: dict[str, object] = {}
        self._instance = instance

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in _BUILDERS:
            raise NotImplementedError(f"unknown host service '{name}'")
        if name not in self._declared:
            raise PermissionError(
                f"widget did not declare host_service '{name}' in its manifest"
            )
        if name not in self._cache:
            self._cache[name] = _BUILDERS[name](self._instance)
        return self._cache[name]
