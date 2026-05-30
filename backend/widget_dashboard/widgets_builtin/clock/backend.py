"""
Clock widget backend — the reference implementation (docs/default-widgets.md).

Demonstrates the minimum a live widget needs:
- a `start()` that launches a background task
- pushing data to the frontend via `ctx.send(...)` (free-form pattern)
- reacting to settings changes
- a clean `stop()` that cancels the task

Uses an internal asyncio task rather than the shared timer event source,
because the clock just needs to tick — it's not watching the system for a
trigger. (A widget that fired on a schedule would use ctx.events with a
'timer' source instead.)
"""

from __future__ import annotations

import asyncio
from datetime import datetime

# Import via the package so the isinstance check in the registry passes.
from widget_dashboard.widget_base import WidgetBase


class Widget(WidgetBase):
    async def start(self) -> None:
        self._task = asyncio.create_task(self._tick())

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def on_settings_change(self, new_settings: dict) -> None:
        # Push an immediate update so the change is visible without waiting
        # for the next tick.
        self._push()

    async def _tick(self) -> None:
        while True:
            self._push()
            # Sleep to the top of the next second so the display stays crisp.
            now = datetime.now()
            await asyncio.sleep(1 - now.microsecond / 1_000_000)

    def _push(self) -> None:
        now = datetime.now()
        use_24h = bool(self.ctx.settings.get("hour24", True))
        show_seconds = bool(self.ctx.settings.get("seconds", True))

        if use_24h:
            time_fmt = "%H:%M:%S" if show_seconds else "%H:%M"
        else:
            time_fmt = "%I:%M:%S %p" if show_seconds else "%I:%M %p"

        self.ctx.send({
            "time": now.strftime(time_fmt),
            "date": now.strftime("%A, %d %B %Y"),
        })
