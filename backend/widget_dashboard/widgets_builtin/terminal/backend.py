"""
Terminal widget (docs/default-widgets.md).

Spawns one `ttyd` per instance and supervises it; the frontend embeds the ttyd
URL in an iframe. ttyd is bound to 127.0.0.1 on a private ephemeral port so the
shell is never reachable off-box (docs/constraints.md "localhost-only").

free-form pattern: the backend pushes {ready, url} (or {error}) once ttyd is up.

Settings: shell (default $SHELL), cwd (working directory), font_size.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket

from widget_dashboard.widget_base import WidgetBase


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Widget(WidgetBase):
    async def start(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        await self._spawn()

    async def stop(self) -> None:
        await self._kill()

    async def on_settings_change(self, new_settings: dict) -> None:
        # Restart ttyd so the new shell / cwd / font take effect.
        await self._kill()
        await self._spawn()

    async def _kill(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    async def _spawn(self) -> None:
        if shutil.which("ttyd") is None:
            self.ctx.send({"error": "ttyd is not installed (apt install ttyd)"})
            return

        s = self.ctx.settings
        shell = s.get("shell") or os.environ.get("SHELL", "/bin/bash")
        cwd = s.get("cwd") or os.path.expanduser("~")
        font = int(s.get("font_size", 14) or 14)
        port = _free_port()

        try:
            self._proc = await asyncio.create_subprocess_exec(
                "ttyd", "-p", str(port), "-i", "127.0.0.1", "-W",
                "-t", f"fontSize={font}", "-t", "disableLeaveAlert=true",
                shell,
                cwd=cwd if os.path.isdir(cwd) else None,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001
            self.ctx.send({"error": f"could not start ttyd: {e}"})
            return

        # Give ttyd a moment to bind, then hand the URL to the frontend.
        await asyncio.sleep(0.4)
        if self._proc.returncode is not None:
            self.ctx.send({"error": "ttyd exited immediately"})
            return
        self.ctx.send({"ready": True, "url": f"http://127.0.0.1:{port}"})
