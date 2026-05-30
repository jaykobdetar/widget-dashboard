"""
Shell Command widget (docs/default-widgets.md).

Runs a configured command and publishes its output as backend state. Uses the
state_intents pattern (docs/widgets.md 4.5): the backend owns a state object,
the frontend renders from it and sends a `run` intent for on-click refresh.

Settings:
  command   : the shell command to run
  mode      : "interval" | "on-click"
  interval  : seconds between runs (interval mode)
  display   : "text" | "number" | "sparkline" | "pill" | "table"
  regex     : optional; first capture group becomes the value
  label     : optional caption shown under the value
  thresholds: optional [{op:">"|">="|"<"|"<="|"==", value, color}] for pills,
              evaluated top-to-bottom; first match wins
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
from datetime import datetime

from widget_dashboard.widget_base import WidgetBase

HISTORY_LEN = 60


def _terminal_argv(cwd: str | None, inner: list[str]) -> list[str] | None:
    """argv to run `inner` in the first available terminal emulator, opening in
    `cwd` where the terminal supports a directory flag (otherwise the spawn's
    cwd is used). Returns None if no terminal is found."""
    wd = bool(cwd)
    table = [
        ("gnome-terminal", lambda: ["gnome-terminal", *([f"--working-directory={cwd}"] if wd else []), "--", *inner]),
        ("konsole",        lambda: ["konsole", *(["--workdir", cwd] if wd else []), "-e", *inner]),
        ("xfce4-terminal", lambda: ["xfce4-terminal", *([f"--working-directory={cwd}"] if wd else []), "-x", *inner]),
        ("tilix",          lambda: ["tilix", *([f"--working-directory={cwd}"] if wd else []), "-e", *inner]),
        ("kitty",          lambda: ["kitty", *(["--directory", cwd] if wd else []), *inner]),
        ("alacritty",      lambda: ["alacritty", *(["--working-directory", cwd] if wd else []), "-e", *inner]),
        ("xterm",          lambda: ["xterm", "-e", *inner]),
        ("x-terminal-emulator", lambda: ["x-terminal-emulator", "-e", *inner]),
    ]
    for name, build in table:
        if shutil.which(name):
            return build()
    return None


class Widget(WidgetBase):
    async def start(self) -> None:
        self._task: asyncio.Task | None = None
        self._history: list[float] = []
        self._state: dict = {
            "value": None, "raw": "", "error": None,
            "history": [], "running": False, "ts": None,
        }
        # On tab load a configured interval widget populates immediately…
        self._restart(immediate=True)

    async def stop(self) -> None:
        self._cancel()

    async def get_initial_state(self) -> dict:
        return self._publish_state()

    async def on_intent(self, intent_type: str, payload: dict) -> None:
        if intent_type == "run":
            await self._run_once()

    async def on_settings_change(self, new_settings: dict) -> None:
        # …but editing the command must NOT launch it. Interval mode waits for
        # its next tick; on-click mode waits for a click. This avoids firing a
        # command (which may have side effects) the moment it's typed in.
        self._history.clear()
        self._restart(immediate=False)
        # Push the new state (mode/display/label) WITHOUT running the command,
        # so the frontend learns e.g. that it's now on-click and becomes
        # clickable. (Without this the click handler stays gated on the old mode.)
        self.ctx.set_state(self._publish_state())

    # --- internals ---

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _restart(self, immediate: bool) -> None:
        self._cancel()
        # Terminal-launch mode is always a manual launcher — never auto-run on
        # an interval (we don't want to spawn a terminal on every tab load).
        if self.ctx.settings.get("terminal"):
            return
        mode = self.ctx.settings.get("mode", "interval")
        if mode == "interval" and self.ctx.settings.get("command"):
            self._task = asyncio.create_task(self._loop(immediate))
        # on-click mode (or no command): nothing runs until a 'run' intent.

    async def _loop(self, immediate: bool) -> None:
        interval = float(self.ctx.settings.get("interval", 5) or 5)
        if immediate:
            await self._run_once()
        while True:
            await asyncio.sleep(max(0.5, interval))
            await self._run_once()

    async def _run_once(self) -> None:
        command = self.ctx.settings.get("command", "")
        if not command:
            self._state["error"] = "no command configured"
            self.ctx.set_state(self._publish_state())
            return
        # Optional working directory the command runs in (~ is expanded).
        cwd = (self.ctx.settings.get("cwd", "") or "").strip()
        if cwd:
            cwd = os.path.expanduser(cwd)
            if not os.path.isdir(cwd):
                self._state["raw"] = ""
                self._state["value"] = None
                self._state["error"] = f"directory not found: {cwd}"
                self.ctx.set_state(self._publish_state())
                return
        else:
            cwd = None

        # Launch mode: open the command in a new, detached terminal window
        # instead of capturing its output (so it outlives the dashboard).
        if self.ctx.settings.get("terminal"):
            await self._launch_in_terminal(command, cwd)
            return

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            raw = out.decode("utf-8", "replace").strip()
            self._state["error"] = None if proc.returncode == 0 else f"exit {proc.returncode}"
        except asyncio.TimeoutError:
            # Kill and reap the timed-out command, otherwise it keeps running
            # detached and the next interval tick spawns another on top of it.
            proc.kill()
            await proc.wait()
            raw, self._state["error"] = "", "timeout"
        except Exception as e:  # noqa: BLE001
            raw, self._state["error"] = "", str(e)

        value = self._parse(raw)
        self._state["raw"] = raw
        self._state["value"] = value
        self._state["ts"] = datetime.now().strftime("%H:%M:%S")

        num = self._as_number(value)
        if num is not None:
            self._history.append(num)
            self._history = self._history[-HISTORY_LEN:]
        self.ctx.set_state(self._publish_state())

    async def _launch_in_terminal(self, command: str, cwd: str | None) -> None:
        # Run the command in a shell that holds the window open afterwards so
        # output stays visible / the program keeps running.
        inner = ["bash", "-lc",
                 f"{command}; ec=$?; echo; "
                 f"printf '\\n[process exited %s — press Enter to close] ' \"$ec\"; read _"]
        argv = _terminal_argv(cwd, inner)
        if argv is None:
            self._state["error"] = "no terminal emulator found (install gnome-terminal/xterm)"
            self.ctx.set_state(self._publish_state())
            return
        try:
            await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,   # detach: survives the dashboard exiting
            )
            self._state["error"] = None
            self._state["raw"] = ""
            self._state["value"] = "launched"
            self._state["ts"] = datetime.now().strftime("%H:%M:%S")
        except Exception as e:  # noqa: BLE001
            self._state["error"] = str(e)
        self.ctx.set_state(self._publish_state())

    def _parse(self, raw: str):
        regex = self.ctx.settings.get("regex")
        if regex:
            m = re.search(regex, raw)
            if m:
                return m.group(1) if m.groups() else m.group(0)
            return ""
        return raw

    @staticmethod
    def _as_number(value):
        try:
            return float(str(value).strip())
        except (ValueError, TypeError):
            return None

    def _publish_state(self) -> dict:
        s = self.ctx.settings
        return {
            **self._state,
            "history": list(self._history),
            "display": s.get("display", "text"),
            "label": s.get("label", ""),
            "thresholds": s.get("thresholds", []),
            "mode": s.get("mode", "interval"),
            "terminal": bool(s.get("terminal")),
            "command": s.get("command", ""),
        }
